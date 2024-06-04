# Sned

> Sned is a general purpose Discord bot designed to make handling your community a breeze!

### [Add it to your server!](https://discord.com/oauth2/authorize?client_id=817730141722902548&permissions=1494984682710&scope=applications.commands%20bot)

### Main features:
- Powerful moderation commands
- Intuitive settings menu
- AutoMod
- Report system for users to anonymously report messages to your team
- Customizable logging to keep moderators accountable
- Toxicity filtering via [Perspective](https://www.perspectiveapi.com/)
- Rolebuttons for letting users pick their roles
- Starboard
- Tags system
- Reminders with snoozing and additional recipient support
- Fun commands such as tic-tac-toe and typeracer
- Much much more!


### Configuration:

To get started with setting up the bot on a server you have `Manage Server` permissions on, simply type `/settings`!

### Development:

If you'd like to contribute to Sned, or host it locally, you need the following utilities:

- [`make`](https://www.gnu.org/software/make/)
- [`docker`](https://www.docker.com/get-started/)
- [`python`](https://www.python.org/downloads/) - 3.10 or higher
- [`poetry`](https://python-poetry.org/docs/) - for managing python dependencies

To deploy the bot, create and fill out `.env`, you can see an example in `.env_example`, along with `config.py`, for which you can find an example in `config_example.py`.
Then simply run `make deploy` to start the bot in the background along with it's database.

If you'd like to contribute, please make sure to run [`nox`](https://nox.thea.codes/en/stable/index.html) in the project folder before submitting your changes. This should format all your code to match the project.
