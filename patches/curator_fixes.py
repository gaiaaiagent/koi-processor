"""
Patches to add missing methods to DailyCurator and WeeklyAggregator
"""

# Add to DailyCurator class:
async def initialize(self):
    """Initialize the Daily Curator"""
    logger.info("Initializing Daily Curator...")
    self.conn = None
    try:
        self.conn = await asyncpg.connect(self.db_url)
        logger.info("Daily Curator initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize Daily Curator: {e}")
        return False

async def cleanup(self):
    """Cleanup Daily Curator resources"""
    logger.info("Cleaning up Daily Curator...")
    if hasattr(self, 'conn') and self.conn:
        await self.conn.close()
    logger.info("Daily Curator cleaned up")
    return True


# Add to WeeklyAggregator class:
async def initialize(self):
    """Initialize the Weekly Aggregator"""
    logger.info("Initializing Weekly Aggregator...")
    self.conn = None
    try:
        self.conn = await asyncpg.connect(self.db_url)
        logger.info("Weekly Aggregator initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize Weekly Aggregator: {e}")
        return False

async def cleanup(self):
    """Cleanup Weekly Aggregator resources"""
    logger.info("Cleaning up Weekly Aggregator...")
    if hasattr(self, 'conn') and self.conn:
        await self.conn.close()
    logger.info("Weekly Aggregator cleaned up")
    return True