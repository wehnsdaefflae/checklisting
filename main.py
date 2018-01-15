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
    # https://coinmarketcap.com/currencies/attention-token-of-media/ , all_coins[x]["id"]

    # {'id': 'bitcoin',
    #  'name': 'Bitcoin',
    #  'symbol': 'BTC',
    #  'rank': '1',
    #  'price_usd': '14240.1',
    #  'price_btc': '1.0',
    #  '24h_volume_usd': '11785900000.0',
    #  'market_cap_usd': '239292420412',
    #  'available_supply': '16804125.0',
    #  'total_supply': '16804125.0',
    #  'max_supply': '21000000.0',
    #  'percent_change_1h': '2.52',
    #  'percent_change_24h': '4.65',
    #  'percent_change_7d': '-7.1',
    #  'last_updated': '1516021762',
    #  'price_eur': '11600.7971457',
    #  '24h_volume_eur': '9601465936.3',
    #  'market_cap_eur': '194941245336',
    #  'cached': False}

    cmc = Market()
    all_coins = dict()
    for each_coin in cmc.ticker(limit=0, convert="EUR"):
        lowercase_symbol = each_coin["symbol"].lower()
        try:
            value = float(each_coin["price_eur"])
        except TypeError or KeyError:
            value = -1.

        id_str = each_coin.get("id", "")
        all_coins[lowercase_symbol] = {"id": id_str, "price_eur": value}

    return all_coins


def linked_symbols_string(symbols, identity_dict):
    coin_list = []
    for x in sorted(symbols):
        id_str = identity_dict.get(x, "")
        if 0 < len(id_str):
            coin_list.append("{:s} ([{:s}](https://coinmarketcap.com/currencies/{:s}/))".format(x, id_str, id_str))
        else:
            coin_list.append("{:s}".format(x))
    return ", ".join(coin_list)


class Bot:
    def __init__(self, token_str):
        self.updater = Updater(token_str)
        self.jobs = dict()
        self.delta = set()
        self.coin_ids = dict()

        if os.path.isfile(LISTED_PATH):
            root_logger.info("Loading previous listing from <{:s}>...".format(LISTED_PATH))
            with open(LISTED_PATH, mode="r") as listed_file:
                self.listing = set(json.load(listed_file))
        else:
            root_logger.info("No previous listing at <{:s}>...".format(LISTED_PATH))
            self.listing = set()

    def update_listing(self, coin_info):
        for k, v in coin_info.items():
            self.coin_ids[k] = v.get("id", "")

        listing = set(coin_info.keys())
        self.delta.clear()
        for each_coin in self.listing | listing:
            if (each_coin in self.listing) != (each_coin in listing):
                self.delta.add(each_coin)

        self.listing.clear()
        self.listing.update(listing)

    def __iteration__(self, bot, job):
        chat_id = job.context
        root_logger.info("Job iteration for ID {:d}.".format(chat_id))

        # bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        symbols = Bot.get_symbols(chat_id)

        added, removed = set(), set()
        for each_symbol in symbols & self.delta:
            if each_symbol in self.listing:
                added.add(each_symbol)
            else:
                removed.add(each_symbol)

        if 0 < len(added):
            message = "ADDED: " + linked_symbols_string(added, self.coin_ids)
            bot.send_message(chat_id=job.context, text=message, parse_mode=ParseMode.MARKDOWN)

        if 0 < len(removed):
            message = "REMOVED: " + ", ".join(sorted(removed))
            bot.send_message(chat_id=job.context, text=message)

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
                root_logger.error("Error while parsing JSON in <{}>! Defaulting to empty list.\n{:s}".format(json_path, ve))
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
            listed = symbols & self.listing
            if 0 < len(listed):
                message = linked_symbols_string(listed, self.coin_ids)
                update.message.reply_text("LISTED: " + message, parse_mode=ParseMode.MARKDOWN)
            if len(listed) < len(symbols):
                update.message.reply_text("NOT LISTED: " + ", ".join(sorted(symbols - self.listing)))

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
            coin_dict = get_debug_prices() if DEBUG else get_cmc_prices()

            listed_coins = set(coin_dict.keys())
            root_logger.info("Received {:d} coins.".format(len(listed_coins)))

            root_logger.info("Saving listed coins to <{}>.".format(LISTED_PATH))
            with open(LISTED_PATH, mode="w") as file:
                json.dump(sorted(listed_coins), file)

            root_logger.info("Updating bot state.")
            new_bot.update_listing(coin_dict)

        except Exception as e:
            root_logger.error("Caught error:\n{}\nSkipping cycle...".format(e))
