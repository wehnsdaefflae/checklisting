import json

import os

import shutil
from coinmarketcap import Market
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
import logging

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)


def get_debug_prices():
    with open("resources/debug_coins.json", mode="r") as debug_file:
        return json.load(debug_file)


def get_cmc_prices():
    cmc = Market()
    all_coins = cmc.ticker(limit=0, convert="EUR")
    print("Received {:d} coins...".format(len(all_coins)))

    coin_dict = dict()
    for each_coin in all_coins:
        lowercase_symbol = each_coin["symbol"].lower()
        try:
            value = float(each_coin["price_eur"])
        except TypeError or KeyError:
            value = -1.

        coin_dict[lowercase_symbol] = value
    return coin_dict


def format_change(change_dict):
    lines = []
    for each_symbol, each_value in change_dict.items():
        if each_value == 0:
            text = "unchanged"
        elif each_value < 0:
            text = "removed"
        else:
            text = "added"
        lines.append("{:<5s} {:s}".format(each_symbol + ":", text))
    return "\n".join(lines)


class CoinState:
    def __init__(self):
        self.last_coins = set()

    @staticmethod
    def __delta__(last_state, this_state):
        changes = set()
        for each_symbol in last_state | this_state:
            if (each_symbol in this_state) != (each_symbol in last_state):
                changes.add(each_symbol)
        return changes

    def get_change(self, symbols, this_coins):
        change = dict()
        if 0 < len(self.last_coins):
            changed_coins = CoinState.__delta__(self.last_coins, this_coins)

            if len(symbols) < 1:
                symbols = self.last_coins | this_coins

            for each_symbol in symbols:
                if each_symbol not in changed_coins:
                    value = 0
                elif each_symbol in this_coins:
                    value = 1
                else:
                    value = -1
                change[each_symbol] = value

            self.last_coins.clear()

        self.last_coins.update(this_coins)
        return change


class Bot:
    def __init__(self, token_str):
        self.updater = Updater(token_str)
        self.interval = 300
        self.jobs = dict()
        self.cs = CoinState()

    def __iteration__(self, bot, job):
        chat_id = job.context

        # read relevant symbols
        symbols = Bot.get_symbols(chat_id)

        # read current prices
        price_dict = get_debug_prices()
        # price_dict = get_cmc_prices()

        change = self.cs.get_change(symbols, set(price_dict.keys()))
        values = change.values()

        if 1 in values or -1 in values:
            bot.send_message(chat_id=job.context, text=format_change(change))

    @staticmethod
    def __unknown__(bot, update):
        txt = "Unknown command. Use '/start', '/stop', '/list', '/add <smb>', '/remove <smb>', or '/purge'."
        bot.send_message(chat_id=update.message.chat_id, text=txt)

    @staticmethod
    def get_symbols(chat_id):
        json_path = "chats/{:d}/symbols.json".format(chat_id)
        if not os.path.isfile(json_path):
            return []
        with open(json_path, mode="r") as file:
            try:
                symbols = json.load(file)
            except ValueError:
                print("Error while parsing JSON in <{}>! Defaulting to empty list.".format(json_path))
                symbols = [">json error<"]
            except FileNotFoundError:
                print("File <{}> not found! Defaulting to empty list.".format(json_path))
                symbols = [">file error<"]
        return sorted({x.lower() for x in symbols})

    @staticmethod
    def add_symbol(symbol, chat_id):
        file_path = "chats/{:d}/symbols.json".format(chat_id)
        symbols = Bot.get_symbols(chat_id)
        if symbol not in symbols:
            symbols.append(symbol)
        with open(file_path, mode="w") as file:
            json.dump(symbols, file)

    @staticmethod
    def remove_symbol(symbol, chat_id):
        file_path = "chats/{:d}/symbols.json".format(chat_id)
        symbols = Bot.get_symbols(chat_id)
        if symbol in symbols:
            symbols.remove(symbol)
        with open(file_path, mode="w") as file:
            json.dump(symbols, file)

    def __start__(self, bot, update, job_queue):
        chat_id = update.message.chat_id
        if chat_id in self.jobs:
            update.message.reply_text("Service id {:d} already running! Write '/stop'.".format(chat_id))
            return
        update.message.reply_text("Starting service id {:d}...".format(chat_id))
        # bot.send_message(chat_id=chat_id, text="Starting service id {:d}...".format(chat_id))
        directory = "chats/{:d}/".format(chat_id)
        if not os.path.isdir(directory):
            os.mkdir(directory)
        job = job_queue.run_repeating(self.__iteration__, context=chat_id, interval=self.interval, first=self.interval)
        self.jobs[chat_id] = job

        self.__iteration__(bot, job)
        self.__list_symbols__(bot, update)

    def __purge_id__(self, bot, update):
        chat_id = update.message.chat_id
        self.__list_symbols__(bot, update)
        self.__stop__(bot, update)
        update.message.reply_text("Purging id {:d}".format(chat_id))
        shutil.rmtree("chats/{:d}/".format(chat_id))

    def __list_symbols__(self, bot, update):
        chat_id = update.message.chat_id

        symbols = Bot.get_symbols(chat_id)
        on_exchange = self.cs.last_coins

        lines = []
        for each_symbol in symbols:
            line = "{:<5s} {}".format(each_symbol + ":", "listed" if each_symbol in on_exchange else "not listed")
            lines.append(line)

        if len(lines) < 1:
            update.message.reply_text("Watching:\nNone")
            update.message.reply_text("Watch symbols with '\\add <smb>'.")
        else:
            update.message.reply_text("Watching:\n" + "\n".join(lines))

        if chat_id not in self.jobs:
            update.message.reply_text("Service id {:d} not started yet! Write '/start'.".format(chat_id))

    def __stop__(self, bot, update):
        chat_id = update.message.chat_id
        job = self.jobs.get(chat_id)
        if job is None:
            update.message.reply_text("Service id {:d} not started yet! Write '/start'.".format(chat_id))
            return

        job.schedule_removal()
        del(self.jobs[chat_id])
        update.message.reply_text("Service id {:d} stopped.".format(chat_id))

    def __add_symbol__(self, bot, update, args):
        chat_id = update.message.chat_id
        symbol = args[0]
        update.message.reply_text("Adding <{:s}> to id {:d}.".format(symbol, chat_id))
        Bot.add_symbol(symbol, chat_id)
        self.__list_symbols__(bot, update)

    def __remove_symbol__(self, bot, update, args):
        chat_id = update.message.chat_id
        symbol = args[0]
        update.message.reply_text("Removing <{:s}> from id {:d}.".format(symbol, chat_id))
        Bot.remove_symbol(symbol, chat_id)
        self.__list_symbols__(bot, update)

    def init(self):
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
        self.updater.idle()


if __name__ == "__main__":
    with open("resources/telegram-token.txt", mode="r") as file:
        token = file.readline().strip()

    new_bot = Bot(token)
    new_bot.init()
