# OBS Support Bot

This repo contains the source code for the OBSProject's Discord support bot.

While the bot is open source, batteries are not included, and there is **no support** for third party users.
Please do not open issues for such use cases.

Pull requests that do not fix issues or are making changes deemed undesireable or unnecessary
for the OBS Project are likely to be rejected. 

## Setup

1. Setup PostgreSQL server version 10+
2. Create database with schema provided in `data/`
3. Install Python 3.8+ and dependencies in `requirements.txt`
4. Create configuration file (example in `data/config.example.toml`)
5. Run `python3.8 runner.py -c path/to/your/config-file.toml`
6. ???
7. Profit!

## License

The OBS Bot source code is licensed under the GNU General Public License v3.
