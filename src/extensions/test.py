import logging

from src.models.client import SnedClient, SnedPlugin

logger = logging.getLogger(__name__)

plugin = SnedPlugin("Test")


def load(client: SnedClient) -> None:
    # client.add_plugin(test)
    pass


def unload(client: SnedClient) -> None:
    # client.remove_plugin(test)
    pass


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
