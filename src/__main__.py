#!/usr/bin/python3

import asyncio
import logging
import os
import pathlib
import platform
import re

from src.models.client import SnedClient

DOTENV_REGEX = re.compile(r"^(?P<identifier>[A-Za-z_]+[A-Za-z0-9_]*)=(?P<value>[^#]+)(#.*)?$")
BASE_DIR = str(pathlib.Path(os.path.abspath(__file__)).parents[1])

if int(platform.python_version_tuple()[1]) < 10:
    logging.fatal("Python version must be 3.10 or greater! Exiting...")
    exit(1)

try:
    with open(os.path.join(BASE_DIR, ".env")) as env:
        for line in env.readlines():
            match = DOTENV_REGEX.match(line)
            if not match:
                continue
            os.environ[match.group("identifier")] = match.group("value").strip()

except FileNotFoundError:
    logging.info(".env file not found, using secrets from the environment instead.")

try:
    from .config import Config
except ImportError:
    logging.fatal(
        "Failed loading configuration. Please make sure 'config.py' exists in the root directory of the project and contains valid data."
    )
    exit(1)

if os.name != "nt":  # Lol imagine using Windows
    try:
        import uvloop

        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())  # type: ignore
    except ImportError:
        logging.warn(
            "Failed to import uvloop! Make sure to install it via 'pip install uvloop' for enhanced performance!"
        )

client = SnedClient(Config())

if __name__ == "__main__":
    client.app.run()

# Copyright (C) 2022-present hypergonial

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see: https://www.gnu.org/licenses
