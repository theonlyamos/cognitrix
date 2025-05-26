"""
Main CLI entry point and orchestration.
"""
import asyncio
import logging
import sys

from .args import get_arguments


def main():
    """Main entry point for the Cognitrix CLI."""
    try:
        args = get_arguments()
        
        # Check if the function is async and run appropriately
        if asyncio.iscoroutinefunction(args.func):
            asyncio.run(args.func(args))
        else:
            args.func(args)
            
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(1)
    except Exception as e:
        logging.exception(e)
        sys.exit(1)


if __name__ == "__main__":
    main() 