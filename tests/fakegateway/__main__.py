"""Run the fake Agora standalone: ``uv run python -m tests.fakegateway``; drive it through ``POST /control``."""

from __future__ import annotations

import asyncio

from tests.fakegateway.server import FakeAgora


async def _main() -> None:
    async with FakeAgora() as agora:
        print(f"gateway  {agora.gateway_url}")  # noqa: T201
        print(f"ap       {' '.join(agora.ap_hosts)}")  # noqa: T201
        print(f"rtm      {' '.join(agora.rtm_hosts)}")  # noqa: T201
        print(f"control  {agora.control_url}")  # noqa: T201
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(_main())
