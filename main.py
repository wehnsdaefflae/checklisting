import json

import os
import re

import shutil

import time
from coinmarketcap import Market
from telegram import ParseMode, ChatAction
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters

import logging
log_formatter = logging.Formatter("%(asctime)s [%(threadName)-12.12s] [%(levelname)-5.5s]  %(message)s")
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

file_handler = logging.FileHandler("{}.log".format(time.strftime("%Y-%m-%d_%H-%M-%S")))
file_handler.setFormatter(log_formatter)
root_logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
root_logger.addHandler(console_handler)

DEBUG = False
INTERVAL = 3 if DEBUG else 300
LISTED_PATH = "resources/listed_coins.json"


def get_debug_prices():
    with open("resources/debug_coins.json", mode="r") as debug_file:
        return json.load(debug_file)


def get_cmc_prices():
    cmc = Market()
    return cmc.ticker(limit=0, convert="EUR")


def coin_list_to_dict(coin_list):
    return {coin.get("id", "<no symbol>"): coin for coin in coin_list}


def linked_symbols_string(ids, id_to_coin_dict):
    coin_str_list = []
    for each_id in sorted(ids):
        coin = id_to_coin_dict.get(each_id)
        if coin is None:
            coin_str_list.append("{:s} (no info)".format(each_id))
        else:
            symbol = coin.get("symbol", "<symbol missing>")
            format_string = "[{:s}](https://coinmarketcap.com/currencies/{:s}/) ({:s})"
            coin_str_list.append(format_string.format(each_id, each_id, symbol))

    return ", ".join(coin_str_list)


class Bot:
    def __init__(self, token_str):
        self.updater = Updater(token_str)
        self.jobs = dict()

        self.new_ids = set()
        if os.path.isfile(LISTED_PATH):
            root_logger.info("Loading previously listed IDs from <{:s}>...".format(LISTED_PATH))
            with open(LISTED_PATH, mode="r") as listed_file:
                self.id_to_coin = json.load(listed_file)
        else:
            root_logger.info("No previously listed IDs at <{:s}>...".format(LISTED_PATH))
            self.id_to_coin = dict()

    def update_listing(self, id_to_coin):  # receives list of dicts. change ui to: watching, listed
        listed_ids = set(id_to_coin.keys())
        self.new_ids = listed_ids - set(self.id_to_coin.keys())

        self.id_to_coin.clear()
        self.id_to_coin.update(id_to_coin)

    def __iteration__(self, bot, job):
        chat_id = job.context
        root_logger.info("Job iteration for ID {:d}.".format(chat_id))
        bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        symbols = Bot.get_symbols(chat_id)

        added_ids = set()
        for each_id in self.new_ids:
            coin = self.id_to_coin.get(each_id)
            if coin is None:
                root_logger.error("No coin found for coin ID {:s}! Skipping...".format(each_id))
            elif coin.get("symbol") in symbols:
                added_ids.add(each_id)

        if 0 < len(added_ids):
            message = "ADDED: " + linked_symbols_string(added_ids, self.id_to_coin)
            bot.send_message(chat_id=job.context, text=message, parse_mode=ParseMode.MARKDOWN)

    @staticmethod
    def __unknown__(bot, update):
        txt = "Unknown command. Use '/start', '/stop', '/list', '/add <smb>', '/remove <smb>', or '/purge'."
        bot.send_message(chat_id=update.message.chat_id, text=txt)

    @staticmethod
    def get_symbols(chat_id):
        json_path = "chats/{:d}/symbols.json".format(chat_id)
        if not os.path.isfile(json_path):
            return set()
        with open(json_path, mode="r") as json_file:
            try:
                symbols = json.load(json_file)
            except ValueError as ve:
                format_str = "Error while parsing JSON in <{}>! Defaulting to empty list.\n{:s}"
                root_logger.error(format_str.format(json_path, ve))
                symbols = set()
            except FileNotFoundError as fnf:
                root_logger.error("File <{}> not found! Defaulting to empty list.\n{:s}".format(json_path, fnf))
                symbols = set()
        return {x.lower() for x in symbols}

    @staticmethod
    def add_symbol(symbol, chat_id):
        file_path = "chats/{:d}/symbols.json".format(chat_id)
        symbols = Bot.get_symbols(chat_id)
        if symbol not in symbols:
            symbols.add(symbol)
            with open(file_path, mode="w") as symbol_file:
                json.dump(sorted(symbols), symbol_file)

    @staticmethod
    def remove_symbol(symbol, chat_id):
        file_path = "chats/{:d}/symbols.json".format(chat_id)
        symbols = Bot.get_symbols(chat_id)
        if symbol in symbols:
            symbols.remove(symbol)
            with open(file_path, mode="w") as symbols_file:
                json.dump(sorted(symbols), symbols_file)

    def __start__(self, bot, update, job_queue):
        chat_id = update.message.chat_id
        if chat_id in self.jobs:
            update.message.reply_text("Service ID {:d} already running! Write '/stop'.".format(chat_id))
            return
        update.message.reply_text("Starting service ID {:d}...".format(chat_id))
        # bot.send_message(chat_id=chat_id, text="Starting service ID {:d}...".format(chat_id))
        directory = "chats/{:d}/".format(chat_id)
        if not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        job = job_queue.run_repeating(self.__iteration__, context=chat_id, interval=INTERVAL, first=INTERVAL)
        self.jobs[chat_id] = job

        self.__iteration__(bot, job)
        self.__list_symbols__(bot, update)

    def __purge_id__(self, bot, update):
        chat_id = update.message.chat_id
        self.__list_symbols__(bot, update)
        self.__stop__(bot, update)
        update.message.reply_text("Purging ID {:d}".format(chat_id))
        shutil.rmtree("chats/{:d}/".format(chat_id))

    def __list_symbols__(self, bot, update):
        chat_id = update.message.chat_id
        symbols = Bot.get_symbols(chat_id)

        if len(symbols) < 1:
            update.message.reply_text("Watch list empty! Start watching with '/add <smb>'.")

        else:
            update.message.reply_text("WATCHING:\n" + ", ".join(sorted(symbols)))
            listed_ids = {coin_id for coin_id, coin in self.id_to_coin.items() if coin.get("symbol") in symbols}
            if 0 < len(listed_ids):
                message = linked_symbols_string(listed_ids, self.id_to_coin)
                update.message.reply_text("LISTED:\n" + message, parse_mode=ParseMode.MARKDOWN)

        if chat_id in self.jobs:
            update.message.reply_text("Service ID {:d} running. Write '/stop' to stop.".format(chat_id))
        else:
            update.message.reply_text("Service ID {:d} not running. Write '/start' to start.".format(chat_id))

    def __stop__(self, bot, update):
        chat_id = update.message.chat_id
        job = self.jobs.get(chat_id)
        if job is None:
            update.message.reply_text("Service ID {:d} not started yet! Write '/start'.".format(chat_id))
            return

        job.schedule_removal()
        del(self.jobs[chat_id])
        update.message.reply_text("Service ID {:d} stopped.".format(chat_id))

    def __add_symbol__(self, bot, update, args):
        chat_id = update.message.chat_id
        symbol = args[0]
        update.message.reply_text("Adding <{:s}> to ID {:d}.".format(symbol, chat_id))
        directory = "chats/{:d}/".format(chat_id)
        if not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        Bot.add_symbol(symbol, chat_id)
        self.__list_symbols__(bot, update)

    def __remove_symbol__(self, bot, update, args):
        chat_id = update.message.chat_id
        symbol = args[0]
        update.message.reply_text("Removing <{:s}> from ID {:d}.".format(symbol, chat_id))
        Bot.remove_symbol(symbol, chat_id)
        self.__list_symbols__(bot, update)

    def init(self):
        directory = "chats/"
        if not os.path.isdir(directory):
            os.makedirs(directory)

        chat_ids = [x for x in os.listdir("chats/") if os.path.isdir("chats/" + x) and re.search(r'[0-9]+', x)]
        root_logger.info("Initializing bot with {} chats.".format(len(chat_ids)))

        # start service
        start_handler = CommandHandler("start", self.__start__, pass_job_queue=True)
        self.updater.dispatcher.add_handler(start_handler)

        # stop service
        stop_handler = CommandHandler("stop", self.__stop__)
        self.updater.dispatcher.add_handler(stop_handler)

        # list symbols (show state!)
        list_handler = CommandHandler("list", self.__list_symbols__)
        self.updater.dispatcher.add_handler(list_handler)

        # purge chat id
        purge_handler = CommandHandler("purge", self.__purge_id__)
        self.updater.dispatcher.add_handler(purge_handler)

        # add symbol
        add_handler = CommandHandler("add", self.__add_symbol__, pass_args=True)
        self.updater.dispatcher.add_handler(add_handler)

        # remove symbol
        remove_handler = CommandHandler("remove", self.__remove_symbol__, pass_args=True)
        self.updater.dispatcher.add_handler(remove_handler)

        unknown_handler = MessageHandler(Filters.command, Bot.__unknown__)
        self.updater.dispatcher.add_handler(unknown_handler)

        self.updater.start_polling()


if __name__ == "__main__":
    token_path = "resources/telegram-token.txt"
    root_logger.info("Reading telegram token from <{:s}>.".format(token_path))
    with open(token_path, mode="r") as file:
        token = file.readline().strip()

    new_bot = Bot(token)
    new_bot.init()

    interval = INTERVAL
    while True:
        waited = 0
        root_logger.info("Waiting {:d} seconds.".format(interval))
        while waited < interval:
            # print("  Waiting {:d} more seconds...".format(interval - waited))
            time.sleep(1)
            waited += 1

        root_logger.info("Getting coinmarketcap data.")
        try:
            coins = get_debug_prices() if DEBUG else get_cmc_prices()
            for each_coin in coins:
                each_coin["symbol"] = each_coin.get("symbol", "").lower()
            root_logger.info("Received {:d} coins.".format(len(coins)))

            coin_dict = coin_list_to_dict(coins)

            root_logger.info("Saving listed coins to <{}>.".format(LISTED_PATH))
            with open(LISTED_PATH, mode="w") as file:
                json.dump(coin_dict, file, indent=2)

            root_logger.info("Updating bot state.")
            new_bot.update_listing(coin_dict)

        except Exception as e:
            root_logger.error("Caught error:\n{}\nSkipping cycle...".format(e))
