import asyncio
import logging

from shared.models import Base, RunLog

from worker import client, db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def main() -> None:
    Base.metadata.create_all(db.engine, tables=[RunLog.__table__])
    log.info("worker started")
    await client.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
