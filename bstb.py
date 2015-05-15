#!/usr/bin/env python3

import datetime
import json
import logging
import pickle
import re
import sys
import threading
import time
import urllib.request
from ssl import SSLError
from urllib.error import URLError

import dateutil.parser
from lxml import html
import schedule

import sirc

tz_list = pickle.load(open("tzs", "rb"))


class BSTB(sirc.TwitchIRCClient):
    """The Better StreamTime Bot."""

    def __init__(self, *args, **kwargs):
        """Initializes BSTB. Also see sirc.TwitchIRCClient."""
        super().__init__(*args, **kwargs)
        self.times = dict()
        self.tz = -time.timezone

    def handle_privmsg(self, channel, user, msg):
        """Handles PRIVMSG commands by checking on commands handled by
        BSTB and responding accordingly.
        """
        lmsg = msg.lower()
        if lmsg.startswith("!streamtime"):
            logging.info(
                "STREAMTIME REQUEST ON CHANNEL %s BY USER %s", channel, user)
            if self.times[channel].get("overwrite_msg", None) is not None:
                self.queue_message(
                    channel, self.times[channel]["overwrite_msg"])
                return
            if self.times[channel]["live"]:
                self.queue_message(
                    channel, "It's live! F5!")
                return
            if self.channels[channel].get("overwrite_time", None) is not None:
                ow_ts = self.channels[channel]["overwrite_time"].timestamp()
                if time.time() > ow_ts:
                    self.queue_message(channel, "Stream in {}".format(
                        self.countdown(ow_ts)))
                    return
                elif time.time() - ow_ts < 300:
                    self.queue_message(
                        channel, "The stream should have started a second ago!"
                    )
                    return
            next_stream = float("inf")
            now = time.time()
            desc = None
            tzadd = self.times[channel]["tz"] * 60 + self.tz
            for t in self.times[channel]["times"]:
                ts = t[0].timestamp() + tzadd
                if now - 300 < ts < next_stream:
                    next_stream = ts
                    desc = t[1]
            if next_stream == float("inf"):
                self.queue_message(
                    channel, "There are currently no scheduled streams :(")
            else:
                cd = self.countdown(next_stream)
                if cd is None:
                    self.queue_message(
                        channel, "The stream should have started a second ago!"
                    )
                    return
                if desc is not None:
                    if desc.lower().endswith("stream"):
                        self.queue_message(channel, "{} in {}".format(
                            desc, cd
                        ))
                    else:
                        self.queue_message(channel, "{} in {}".format(
                            desc, cd
                        ))
                else:
                    self.queue_message(channel, "Stream in {}".format(cd))

        elif lmsg.startswith("!bstb"):
            not_mod = (user not in self.channels[channel]["ops"] and user !=
                       "jazzpi")
            rest = lmsg[5:].strip()
            self.logger.debug("!bstb message with rest: {}".format(rest))
            if rest == "":
                self.respond(
                    channel, user, "I am a bot created by jazzpi that "
                    "automatically reads the streamtime from the stream "
                    "description and spits out a countdown if you type "
                    "'!streamtime'.")
            elif rest == "help":
                self.respond(
                    channel, user, "If you are a mod, you can use '!bstb "
                    "overwrite_msg' or '!bstb overwrite_time' to overwrite the"
                    " output of BSTB and '!bstb overwrite_discard' to go back "
                    " to normal.")
            elif rest == "overwrite_msg":
                self.respond(
                    channel, user, "Type '!bstb overwrite_msg "
                    "your_message_here' to overwrite the output.")
            elif rest.startswith("overwrite_msg "):
                if not_mod:
                    self.mod_only(channel, user)
                    return
                self.times[channel]["overwrite_msg"] = rest[14:]
                self.respond(
                    channel, user, "!streamtime output has been overwritten.")
            elif rest == "overwrite_time":
                self.respond(
                    channel, user, "Overwrite time in the format '!bstb "
                    "overwrite_time YYYY-MM-DD hh:mm [AM|PM] timezone'")
            elif rest.startswith("overwrite_time "):
                if not_mod:
                    self.mod_only(channel, user)
                    return
                time_string, tz = rest[15:].rsplit(" ", 1)
                try:
                    stime = dateutil.parser.parse(time_string)
                except ValueError:
                    self.respond(
                        channel, user, "Couldn't parse time - Try '!bstb "
                        "overwrite_time YYYY-MM-DD hh:mm [AM|PM] timezone'")
                    return
                tz = self.parse_timezone(tz)
                stime -= datetime.timedelta(minutes=tz)
                self.times[channel]["overwrite_time"] = stime
                self.respond(
                    channel, user, "Time for next stream overwritten with {}."
                    "".format(stime.strftime('%Y-%m-%d %H:%M UTC')))
            elif rest == "overwrite_discard":
                if not_mod:
                    self.mod_only(channel, user)
                    return
                self.times[channel]["overwrite_msg"] = None
                self.channels[channel]["overwrite_time"] = None
                self.respond(channel, user, "Overwrite discarded.")

        elif lmsg.strip().split(" ")[0] == "!uptime":
            if self.times[channel]["live"]:
                self.queue_message(
                    channel, "The stream has been live for {}".format(
                        self.countdown(
                            2 * time.time() -
                            self.times[channel]["live_time"])))
            else:
                self.queue_message(
                    channel, "The stream is not live.")

    def respond(self, channel, user, message):
        """Responds to someone in `channel` in the format
        '`user` -> `message`'.
        """
        self.queue_message(channel, "{} -> {}".format(user, message))

    def mod_only(channel, user):
        """Tells `user` in `channel` that they can't run a command."""
        self.respond(
            channel, user, "This command is mod-only. If you are "
            "mod, try again in a minute or so - Twitch sometimes "
            "randomly removes mod status from people.")

    def join_channel(self, channel):
        """Joins a channel."""
        super().join_channel(channel)
        self.get_streamtimes(channel)
        schedule.every(30).seconds.do(self.get_streamtimes, channel)

    def get_streamtimes(self, channel):
        """Retrieves the stream times from the Stream Schedule Panel of
        the given channel.
        """
        if self.times.get(channel) is None:
            self.times[channel] = dict()
        # First, let's check if the channel is actually live already
        self.logger.debug("REQUESTING kraken/streams")
        # Sometimes, SSLErrors get thrown - just ignore that
        try:
            with urllib.request.urlopen(
                    "https://api.twitch.tv/kraken/streams/" +
                    channel.replace("#", "", 1)) as req:
                stream_json = json.JSONDecoder().decode(req.read().decode())
        except (SSLError, URLError):
            self.logger.warn("SSLError on trying to fetch /kraken/streams!")
            return
        if stream_json["stream"] is not None:
            if not self.times[channel]["live"]:
                self.times[channel]["live_time"] = time.time()
            self.times[channel]["live"] = True
        else:
            self.times[channel]["live"] = False
        self.logger.debug("REQUESTING api/channels")
        try:
            with urllib.request.urlopen(
                    "https://api.twitch.tv/api/channels/" +
                    channel.replace("#", "", 1) + "/panels") as req:
                panels_json = json.JSONDecoder().decode(req.read().decode())
        except (SSLError, URLError):
            self.logger.warn("SSLError on trying to fetch /api/panels!")
            return

        for panel in panels_json:
            if panel["data"].get("title", "").lower() == "stream schedule" or \
                    panel["data"].get("title", "").lower() == "schedule":
                # Get text content only of all lines in the stream
                # schedule panel
                sched = html.fromstring(panel["html_description"])
                sched = sched.text_content().split("\n")
                if sched[0].lower().startswith("all times in "):
                    self.times[channel]["tz"] = self.parse_timezone(sched[0])
                    self.times[channel]["times"] = self.parse_times(sched[1:])
                else:
                    self.times[channel]["tz"] = 0
                    self.times[channel]["times"] = self.parse_times(sched)

    @staticmethod
    def parse_timezone(tz_string):
        """Parses a timezone string. Returns offset from UTC in minutes."""
        # Check if timezone is given in UTC+00(:)00 format
        m = re.match("(.{13})?UTC([+-])(\d{2}):?(\d{2})", tz_string)
        if m is not None:
            tz = int(m.group(3)) * 60 + int(m.group(4))
            if m.group(2) == "-":
                tz *= -1
            return tz
        # Check if timezone is given as timezone code
        m = re.match("(.{13})?([A-Z]{1,5})", tz_string)
        if m is not None:
            tz = tz_list.get(m.group(2))
            if tz is None:
                logging.getLogger(__name__).warn(
                    "Met unknown timezone code:%s", m.group(2))
                tz = 0
            return tz
        logging.getLogger(__name__).warn("No timezone given, using UTC!")
        return 0

    @classmethod
    def parse_times(cls, schedule):
        """Parses a schedule string. Returns times with descriptions."""
        times = []
        for line in schedule:
            line = line.strip()
            if line == "":
                continue
            m = line.split("|", 1)
            t = m[0]
            try:
                stime = dateutil.parser.parse(t)
            except ValueError:
                # Couldn't parse the string - let's see if it maybe is
                # in format "1st Jan : 1AM"
                if re.match(
                        " *\d+(st|nd|rd|th)? [A-Za-z]+ ?: ?\d+(AM|PM)?", t):
                    try:
                        t = t.replace(":", "")
                        stime = dateutil.parser.parse(t)
                    except ValueError:
                        # Still couldn't parse the string - maybe it has
                        # an end time as well, let's try removing
                        # everything after the last "-"
                        try:
                            stime = dateutil.parser.parse(t.rsplit("-", 1)[0])
                        except ValueError:
                            logging.getLogger(__name__).warn(
                                "Couldn't parse stream time line %s", line)
                            continue
                # Maybe it's Resonance22's format?
                else:
                    m2 = re.match(
                        " *-* ?([A-Za-z]+) (\d+) ?: ?(.*?) at +(\d+):(\d+) "
                        "(AM|PM) \(([A-Za-z]+)\) (.*?) *\(.*?\)", line)
                    if m2 is not None:
                        try:
                            stime = dateutil.parser.parse(
                                m2.group(1) + " " + m2.group(2) + " " +
                                m2.group(4) + ":" + m2.group(5) + " " +
                                m2.group(6))
                            desc = m2.group(3) + " " + m2.group(8)
                            stime -= datetime.timedelta(
                                    minutes=cls.parse_timezone(m2.group(7)))
                            times.append((stime, desc))
                        except ValueError:
                            logging.getLogger(__name__).warn(
                                "Couldn't parse stream time line %s", line)
                        continue
                    else:
                        continue
            if len(m) == 2 and m[1] != "":
                desc = m[1].strip()
            else:
                desc = None
            times.append((stime, desc))
        return times

    @staticmethod
    def time_plural(*args):
        if len(args) == 1:
            if args[0] == 1:
                return "1 second"
            else:
                return "{} seconds".format(args[0])
        else:
            s = []
            names = ["week", "day", "hour", "minute", "second"]
            for i, j in enumerate(args):
                if j == 0:
                    continue
                elif j == 1:
                    s.append("1 {}".format(names[i]))
                else:
                    s.append("{} {}s".format(j, names[i]))
            if len(s) == 1:
                return s[0]
            else:
                r = "{} and {}".format(s[-2], s[-1])
                for i in s[-3::-1]:
                    r = "{}, {}".format(i, r)
                return r

    @classmethod
    def countdown(cls, seconds):
        """Converts a timestamp to a countdown."""
        seconds = int(seconds - time.time())
        if seconds < 0:
            logging.getLogger(__name__).warn(
                "No streams? %s - %s < 0", seconds, time.time())
            return None
        minutes = seconds // 60
        seconds = seconds - (minutes * 60)
        hours = minutes // 60
        minutes = minutes - (hours * 60)
        days = hours // 24
        hours = hours - (days * 24)
        weeks = days // 7
        days = days - (weeks * 7)
        return cls.time_plural(weeks, days, hours, minutes, seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s][%(levelname"
                        ")s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    with open("oauth") as fh:
        oauth = fh.read().strip()

    client = BSTB("irc.twitch.tv", 6667, "bstb", pw=oauth)
    logging.getLogger("schedule").propagate = False

    client.run()

    client.join_channel("#resonance22")

while True:
    logging.getLogger(__name__).debug(
        [i.getName() for i in threading.enumerate()])
    if threading.active_count() != 3:
        logging.getLogger(__name__).critical("One thread terminated. Exiting.")
        sys.exit()
    time.sleep(5)
