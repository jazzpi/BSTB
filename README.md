# BSTB

The Better StreamTime Bot - a bot that reads the time to the next stream from
a schedule panel from your Twitch.TV stream description and responds to
`!streamtime` and `!uptime` commands.

## Commands

 - `!streamtime`: A countdown to the next stream
 - `!uptime`: The time the stream has been up for
 - `!bstb [help]`: Extra information on BSTB and its commands
 - `!bstb overwrite_msg`: Overwrite the output of `!streamtime` (Mod only)
 - `!bstb overwrite_time`: Overwrite the time the next stream starts (Mod only)
 - `!bstb overwrite_discard`: Makes `!streamtime`'s output go back to normal
(Mod only)

## Dependencies

BSTB depends on [jazzpi/sirc](https://github.com/jazzpi/sirc) (and the
`schedule` library that sIRC also depends on).

**Disclaimer:** This thing is still somewhat buggy and seems to stop working
every now and then for no reason. Use with caution.
