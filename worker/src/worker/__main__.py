import asyncio
import logging
import os

from shared.models import Base, RunLog
from sqlalchemy import create_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


async def main() -> None:
    Base.metadata.create_all(engine, tables=[RunLog.__table__])
    log.info("worker started")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
