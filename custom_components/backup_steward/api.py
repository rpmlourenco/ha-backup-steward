"""Small authenticated app client. Scheduling is never implemented in Core."""

import asyncio

import aiohttp


class CannotConnect(Exception):
    pass


class InvalidAuth(Exception):
    pass


class Client:
    def __init__(self, session, url, token):
        self.session, self.url, self.token = session, url.rstrip("/"), token

    async def request(self, method, path, body=None):
        try:
            async with self.session.request(
                method,
                self.url + "/v1/" + path,
                json=body,
                headers={"Authorization": "Bearer " + self.token},
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise InvalidAuth
                if response.status >= 300:
                    raise CannotConnect("The app rejected the request")
                return await response.json()
        except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError) as error:
            raise CannotConnect("Cannot contact Backup Steward") from error

    async def status(self):
        return await self.request("GET", "status")

    async def run(self, mode):
        return await self.request("POST", "run", {"mode": mode})

    async def recover(self):
        return await self.request("POST", "recover", {})
