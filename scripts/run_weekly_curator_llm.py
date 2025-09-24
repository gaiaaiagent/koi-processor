#!/usr/bin/env python3
"""
CLI Runner for Weekly Curator with LLM
Uses the new LLM-powered weekly curator that includes ALL content
"""

import sys
import os
import asyncio
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def main():
    """Main execution function"""
    try:
        # Import the new curator's main function directly
        # This avoids duplication since the main() already saves to DB
        from src.content.weekly_curator_llm import main as curator_main

        logger.info("Generating weekly digest with LLM (includes ALL content)...")

        # Run the curator's main function which handles everything
        digest = await curator_main()

        if digest:
            # Return success (digest already saved to DB and exported)
            return 0
        else:
            print("❌ Failed to generate weekly digest")
            return 1

    except Exception as e:
        logger.error(f"Error generating weekly digest: {e}")
        print(f"❌ Error: {e}")
        return 1

if __name__ == "__main__":
    # Run the async main function
    exit_code = asyncio.run(main())
    sys.exit(exit_code)