from maxapi.types import BotCommand

def bot_commands(cabinet: bool = False) -> list[BotCommand]:
    commands = [
        BotCommand(name="start", description="Открыть меню"),
        BotCommand(name="menu", description="Открыть меню"),
    ]
    if not cabinet:
        commands.extend(
            [
                BotCommand(name="notes", description="Мои конспекты"),
                BotCommand(name="search", description="Поиск по конспектам"),
            ]
        )
    commands.extend(
        [
            BotCommand(name="quiz", description="Квиз по теме"),
            BotCommand(name="check", description="Проверить решение"),
            BotCommand(name="errors", description="Квиз по ошибкам"),
        ]
    )
    return commands


BOT_COMMANDS = bot_commands()
