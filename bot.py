from telethon import TelegramClient, sync
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.panel import Panel
from rich import print as rprint
import argparse
import asyncio
import csv
import time
import sys
import os
import json
import random

console = Console()

# Загрузка конфигурации из файла
def load_config():
    config_file = 'config.json'
    if not os.path.exists(config_file):
        default_config = {
            'api_id': 'YOUR_API_ID',  # Замените на ваш api_id (целое число)
            'api_hash': 'YOUR_API_HASH',  # Замените на ваш api_hash (строка)
            'phone': 'YOUR_PHONE'  # В формате: +380123456789
        }
        with open(config_file, 'w') as f:
            json.dump(default_config, f, indent=4)
        console.print("[yellow]Создан файл config.json. Пожалуйста, заполните его корректными данными.[/yellow]")
        sys.exit(1)
    with open(config_file, 'r') as f:
        config = json.load(f)
        # Проверка корректности данных
        if not isinstance(config.get('api_id'), int):
            console.print("[red]Ошибка: api_id должен быть целым числом![/red]")
            sys.exit(1)
        if not isinstance(config.get('api_hash'), str) or len(config.get('api_hash')) < 10:
            console.print("[red]Ошибка: Некорректный api_hash![/red]")
            sys.exit(1)
        if not isinstance(config.get('phone'), str) or not config.get('phone').startswith('+'):
            console.print("[red]Ошибка: Номер телефона должен начинаться с +![/red]")
            sys.exit(1)
        return config

CONFIG = load_config()
API_ID = CONFIG['api_id']
API_HASH = CONFIG['api_hash']
PHONE = CONFIG['phone']

class TelegramSender:
    def __init__(self):
        self.session_file = f'session_{PHONE.replace("+", "")}'  # Убираем + из имени файла сессии
        self.client = TelegramClient(self.session_file, API_ID, API_HASH)
        
    async def start(self):
        try:
            with console.status("[bold green]Подключение к Telegram..."):
                await self.client.connect()
                
                # Если нет сохраненной сессии или требуется повторная авторизация
                if not await self.client.is_user_authorized():
                    console.print("[yellow]Требуется авторизация[/yellow]")
                    try:
                        # Отправляем код подтверждения
                        sent_code = await self.client.send_code_request(PHONE)
                        code = console.input("[yellow]Введите код из Telegram:[/yellow] ")
                        
                        # Пытаемся войти с полученным кодом
                        await self.client.sign_in(phone=PHONE, code=code)
                        
                    except Exception as auth_error:
                        console.print(f"[bold red]Ошибка авторизации: {str(auth_error)}[/bold red]")
                        return False
                
                # Проверяем успешность авторизации
                if await self.client.is_user_authorized():
                    me = await self.client.get_me()
                    console.print(f"[green]Успешно подключено как {me.first_name} (@{me.username})[/green]")
                    return True
                else:
                    console.print("[red]Не удалось авторизоваться![/red]")
                    return False
                    
        except Exception as e:
            console.print(f"[bold red]Ошибка подключения:[/bold red] {str(e)}")
            return False

    async def get_dialogs(self):
        dialogs = []
        with console.status("[bold green]Получение списка групп (включая архив и папки)..."):
            # Получаем все диалоги, включая архивные
            async for dialog in self.client.iter_dialogs(archived=True):
                # Проверяем, является ли диалог группой
                if dialog.is_group:
                    # Получаем дополнительную информацию о группе
                    try:
                        full_chat = await self.client.get_entity(dialog.id)
                        participants_count = getattr(full_chat, 'participants_count', 'N/A')
                        
                        # Определяем статус и местоположение группы
                        status = []
                        if dialog.archived:
                            status.append("Архив")
                        if hasattr(dialog, 'folder_id') and dialog.folder_id:
                            status.append(f"Папка {dialog.folder_id}")
                        
                        status_str = ", ".join(status) if status else "Основной список"
                        
                        dialogs.append({
                            'name': dialog.name or "Без имени",
                            'id': dialog.id,
                            'type': 'Группа',
                            'participants': participants_count,
                            'location': status_str
                        })
                    except Exception as e:
                        console.print(f"[yellow]Пропуск группы {dialog.name}: {str(e)}[/yellow]")
                        continue
        
        # Сортируем группы: сначала основной список, потом архив, внутри по имени
        dialogs.sort(key=lambda x: (x['location'] != "Основной список", x['name'].lower()))
        return dialogs

    async def send_message(self, target_id, message, media_path=None):
        try:
            # Отправка сообщения без статус-бара
            if media_path and os.path.exists(media_path):
                max_caption_length = 1024
                if len(message) > max_caption_length:
                    await self.client.send_file(
                        target_id, 
                        media_path,
                        caption=message[:max_caption_length] + "\n<i>...продолжение следует</i>",
                        parse_mode='html'
                    )
                    remaining_text = message[max_caption_length:]
                    max_message_length = 4096
                    
                    for i in range(0, len(remaining_text), max_message_length):
                        chunk = remaining_text[i:i + max_message_length]
                        await self.client.send_message(
                            target_id,
                            chunk,
                            parse_mode='html'
                        )
                        await asyncio.sleep(2)
                else:
                    await self.client.send_file(
                        target_id, 
                        media_path, 
                        caption=message,
                        parse_mode='html'
                    )
            else:
                await self.client.send_message(
                    target_id,
                    message,
                    parse_mode='html'
                )
            return True
        except Exception as e:
            return False

    async def mass_send(self, message, media_path=None, delay_range=(30, 60)):
        """Массовая рассылка по всем группам с случайной задержкой"""
        dialogs = await self.get_dialogs()
        total = len(dialogs)
        success = 0
        failed = 0
        
        # Создаем один статус-бар для всей рассылки
        with console.status("") as status:
            for i, dialog in enumerate(dialogs, 1):
                try:
                    # Обновляем статус без создания нового
                    status.update(f"[bold blue]Отправка {i}/{total} в {dialog['name']}...")
                    
                    # Отправляем сообщение без дополнительного статуса
                    if await self.send_message(dialog['id'], message, media_path):
                        success += 1
                        console.print(f"[green]✓ {dialog['name']} - успешно[/green]")
                    else:
                        failed += 1
                        console.print(f"[red]✗ {dialog['name']} - ошибка[/red]")
                    
                    # Случайная задержка между отправками
                    delay = random.randint(delay_range[0], delay_range[1])
                    status.update(f"[yellow]Ожидание {delay} секунд перед следующей отправкой...[/yellow]")
                    await asyncio.sleep(delay)
                    
                except Exception as e:
                    failed += 1
                    console.print(f"[red]✗ {dialog['name']} - {str(e)}[/red]")
                    
                    # Если получаем FloodWaitError, делаем длительную паузу
                    if "flood" in str(e).lower():
                        wait_time = 300  # 5 минут
                        status.update(f"[bold red]Обнаружен флуд! Ждем {wait_time} секунд...[/bold red]")
                        await asyncio.sleep(wait_time)
        
        # Выводим итоговую статистику
        console.print("\n[bold cyan]Результаты рассылки:[/bold cyan]")
        console.print(f"[green]Успешно:[/green] {success}")
        console.print(f"[red]Ошибок:[/red] {failed}")
        console.print(f"[blue]Всего групп:[/blue] {total}")
        
        return success, failed, total

    async def load_message_from_file(self, file_path):
        """Загрузка сообщения из текстового файла"""
        try:
            if not os.path.exists(file_path):
                console.print("[red]Файл не найден![/red]")
                return None
                
            with open(file_path, 'r', encoding='utf-8') as f:
                message = f.read().strip()
                
            if not message:
                console.print("[red]Файл пустой![/red]")
                return None
                
            return message
        except Exception as e:
            console.print(f"[red]Ошибка чтения файла: {str(e)}[/red]")
            return None

async def show_menu():
    sender = TelegramSender()
    if not await sender.start():
        console.print("[bold red]Не удалось подключиться к Telegram. Проверьте данные в config.json[/bold red]")
        return

    while True:
        console.clear()
        console.print(Panel.fit(
            "[bold cyan]Telegram Sender[/bold cyan]\n\n"
            "1. Показать список диалогов\n"
            "2. Отправить сообщение в одну группу\n"
            "3. Отправить сообщение с медиа в одну группу\n"
            "4. Массовая рассылка по всем группам\n"
            "5. Загрузить сообщение из файла и отправить\n"
            "6. Загрузить сообщение из файла и сделать массовую рассылку\n"
            "7. Выход",
            title="Главное меню",
            border_style="cyan"
        ))
        
        choice = Prompt.ask("Выберите действие", choices=["1", "2", "3", "4", "5", "6", "7"])
        
        if choice == "7":
            break
            
        if choice == "1":
            dialogs = await sender.get_dialogs()
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("ID", style="dim")
            table.add_column("Имя", style="green")
            table.add_column("Участников", style="cyan")
            table.add_column("Расположение", style="yellow")
            
            # Счетчики для статистики
            stats = {
                "Основной список": 0,
                "Архив": 0,
                "Папки": 0
            }
            
            for dialog in dialogs:
                table.add_row(
                    str(dialog['id']),
                    dialog['name'],
                    str(dialog['participants']),
                    dialog['location']
                )
                
                # Подсчет статистики
                if "Архив" in dialog['location']:
                    stats["Архив"] += 1
                elif "Папка" in dialog['location']:
                    stats["Папки"] += 1
                else:
                    stats["Основной список"] += 1
            
            console.print(table)
            console.print("\n[bold cyan]Статистика групп:[/bold cyan]")
            console.print(f"[green]Всего найдено:[/green] {len(dialogs)}")
            console.print(f"[blue]В основном списке:[/blue] {stats['Основной список']}")
            console.print(f"[yellow]В архиве:[/yellow] {stats['Архив']}")
            console.print(f"[magenta]В папках:[/magenta] {stats['Папки']}")
            
            input("\nНажмите Enter для продолжения...")
            
        elif choice == "2" or choice == "3":
            target_id = Prompt.ask("Введите ID получателя")
            message = Prompt.ask("Введите текст сообщения")
            media_path = None
            
            if choice == "3":
                media_path = Prompt.ask("Введите путь к медиафайлу")
                if not os.path.exists(media_path):
                    console.print("[bold red]Файл не найден![/bold red]")
                    continue
            
            success = await sender.send_message(int(target_id), message, media_path)
            
            if success:
                console.print("[bold green]Сообщение успешно отправлено![/bold green]")
            else:
                console.print("[bold red]Ошибка при отправке сообщения[/bold red]")
            
            input("\nНажмите Enter для продолжения...")
        
        elif choice == "4":
            message = Prompt.ask("Введите текст сообщения")
            media_path = None
            
            if Confirm.ask("Добавить медиафайл?"):
                media_path = Prompt.ask("Введите путь к медиафайлу")
                if not os.path.exists(media_path):
                    console.print("[bold red]Файл не найден![/bold red]")
                    continue
            
            min_delay = int(Prompt.ask("Минимальная задержка между отправками (сек)", default="30"))
            max_delay = int(Prompt.ask("Максимальная задержка между отправками (сек)", default="60"))
            
            if Confirm.ask("Начать рассылку?"):
                success, failed, total = await sender.mass_send(
                    message, 
                    media_path, 
                    delay_range=(min_delay, max_delay)
                )
                
                console.print("\n[bold cyan]Результаты рассылки:[/bold cyan]")
                console.print(f"[green]Успешно отправлено:[/green] {success}")
                console.print(f"[red]Ошибок отправки:[/red] {failed}")
                console.print(f"[blue]Всего групп:[/blue] {total}")
            
            input("\nНажмите Enter для продолжения...")
        
        elif choice == "5":
            file_path = Prompt.ask("Введите путь к текстовому файлу")
            message = await sender.load_message_from_file(file_path)
            
            if message:
                target_id = Prompt.ask("Введите ID получателя")
                media_path = None
                
                if Confirm.ask("Добавить медиафайл?"):
                    media_path = Prompt.ask("Введите путь к медиафайлу")
                    if not os.path.exists(media_path):
                        console.print("[bold red]Файл не найден![/bold red]")
                        continue
                
                success = await sender.send_message(int(target_id), message, media_path)
                
                if success:
                    console.print("[bold green]Сообщение успешно отправлено![/bold green]")
                else:
                    console.print("[bold red]Ошибка при отправке сообщения[/bold red]")
            
            input("\nНажмите Enter для продолжения...")
        
        elif choice == "6":
            file_path = Prompt.ask("Введите путь к текстовому файлу")
            message = await sender.load_message_from_file(file_path)
            
            if message:
                media_path = None
                
                if Confirm.ask("Добавить медиафайл?"):
                    media_path = Prompt.ask("Введите путь к медиафайлу")
                    if not os.path.exists(media_path):
                        console.print("[bold red]Файл не найден![/bold red]")
                        continue
                
                min_delay = int(Prompt.ask("Минимальная задержка между отправками (сек)", default="30"))
                max_delay = int(Prompt.ask("Максимальная задержка между отправками (сек)", default="60"))
                
                if Confirm.ask("Начать рассылку?"):
                    success, failed, total = await sender.mass_send(
                        message, 
                        media_path, 
                        delay_range=(min_delay, max_delay)
                    )
                    
                    console.print("\n[bold cyan]Результаты рассылки:[/bold cyan]")
                    console.print(f"[green]Успешно отправлено:[/green] {success}")
                    console.print(f"[red]Ошибок отправки:[/red] {failed}")
                    console.print(f"[blue]Всего групп:[/blue] {total}")
            
            input("\nНажмите Enter для продолжения...")

    sender.client.disconnect()

def main():
    try:
        asyncio.run(show_menu())
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Программа остановлена пользователем[/bold yellow]")
    except Exception as e:
        console.print(f"\n[bold red]Произошла ошибка:[/bold red] {str(e)}")

if __name__ == '__main__':
    main()