# PyFundaments: A Secure Python Architecture
# Copyright 2008-2025 - Volkan Kücükbudak
# Apache License V. 2
# Repo: https://github.com/VolkanSah/PyFundaments
# main.py
# This is the main entry point of the application.
# It now handles asynchronous initialization of the fundament modules.
import sys
import logging
import asyncio
import os
from typing import Dict, Any, Optional

import importlib.util
import datetime

if 'fundaments' in sys.modules:
    del sys.modules['fundaments']

# We import our core modules from the "fundaments" directory.
try:
    from fundaments.config_handler import config_service
    from fundaments.postgresql import init_db_pool, close_db_pool
    from fundaments.encryption import Encryption
    from fundaments.access_control import AccessControl
    from fundaments.user_handler import UserHandler
    from fundaments.security import Security
    from fundaments.debug import PyFundamentsDebug
except ImportError as e:
    print(f"Error: Failed to import a fundament module: {e}")
    print("Please ensure the modules and dependencies are present.")
    sys.exit(1)
# Debug run    
debug = PyFundamentsDebug()
debug.run()

# Logger configuration - conditional based on ENV
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
log_to_tmp = os.getenv('LOG_TO_TMP', 'false').lower() == 'true'
enable_public_logs = os.getenv('ENABLE_PUBLIC_LOGS', 'true').lower() == 'true'

if enable_public_logs:
    if log_to_tmp:
        log_file = '/tmp/pyfundaments.log'
        logging.basicConfig(
            level=getattr(logging, log_level),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
    else:
        logging.basicConfig(
            level=getattr(logging, log_level),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
else:
    # Silent mode - only critical errors
    logging.basicConfig(level=logging.CRITICAL)

logger = logging.getLogger('main_app_loader')

async def initialize_fundaments() -> Dict[str, Any]:
    """
    Initializes core application services conditionally based on available ENV variables.
    Only loads services for which the required configuration is present.
    """
    logger.info("Starting conditional initialization of fundament modules...")
    
    fundaments = {
        "config": config_service
    }
    
    # --- Database Initialization (PostgreSQL) ---
    # Only initialize if DATABASE_URL is available
    database_url = config_service.get("DATABASE_URL")
    if database_url and database_url != "your_database_dsn_here":
        try:
            db_service = await init_db_pool(database_url)
            if db_service is not None:
                fundaments["db"] = db_service
                logger.info("Database service initialized.")
            else:
                # init_db_pool() returns None instead of raising on failure
                logger.warning("Database pool could not be built, continuing without DB.")
                fundaments["db"] = None
        except Exception as e:
            logger.warning(f"Database initialization failed, continuing without DB: {e}")
            fundaments["db"] = None
    else:
        logger.info("No valid DATABASE_URL found, skipping database initialization.")
        fundaments["db"] = None

    # --- Encryption Initialization ---
    # Only initialize if both encryption keys are available
    master_key = config_service.get("MASTER_ENCRYPTION_KEY")
    persistent_salt = config_service.get("PERSISTENT_ENCRYPTION_SALT")
    
    if master_key and persistent_salt and master_key != "your_256_bit_key_here":
        try:
            encryption_service = Encryption(master_key=master_key, salt=persistent_salt)
            fundaments["encryption"] = encryption_service
            logger.info("Encryption service initialized.")
        except Exception as e:
            logger.warning(f"Encryption initialization failed, continuing without encryption: {e}")
            fundaments["encryption"] = None
    else:
        logger.info("Encryption keys not found or using defaults, skipping encryption initialization.")
        fundaments["encryption"] = None

    # --- Access Control Initialization ---
    # Only initialize if we have a database connection
    if fundaments["db"] is not None:
        try:
            access_control_service = AccessControl()
            fundaments["access_control"] = access_control_service
            logger.info("Access Control service initialized.")
        except Exception as e:
            logger.warning(f"Access Control initialization failed: {e}")
            fundaments["access_control"] = None
    else:
        logger.info("No database available, skipping Access Control initialization.")
        fundaments["access_control"] = None

    # --- User Handler Initialization ---
    # UserHandler is SQLite-based (own Database class, see user_handler.py)
    # and does NOT use the asyncpg pool — wiring it with the Postgres pool
    # was a bug (pool has no fetchone/execute-sync interface).
    # SQLite path: SQLITE_PATH env or fallback next to app/ (HF Spaces: read-only FS
    # may fail — caught below, app continues without user_handler).
    try:
        from fundaments.user_handler import Database as UserDB
        user_db = UserDB()
        user_db.setup_tables()
        user_handler_service = UserHandler(user_db)
        fundaments["user_handler"] = user_handler_service
        logger.info("User Handler service initialized (SQLite store).")
    except Exception as e:
        logger.warning(f"User Handler initialization failed: {e}")
        fundaments["user_handler"] = None

    # --- Security Manager Initialization ---
    # Only initialize if we have the required sub-services
    available_services = {k: v for k, v in fundaments.items() if v is not None and k != "config"}
    
    if len(available_services) >= 1:  # At least one service beyond config
        try:
            # Filter out None services for Security manager
            fundament_services = {
                k: v for k, v in {
                    "user_handler": fundaments.get("user_handler"),
                    "access_control": fundaments.get("access_control"),
                    "encryption": fundaments.get("encryption")
                }.items() if v is not None
            }
            
            if fundament_services:  # Only if we have actual services
                security_service = Security(fundament_services)
                fundaments["security"] = security_service
                logger.info("Security manager initialized.")
            else:
                logger.info("No services available for Security manager, skipping initialization.")
                fundaments["security"] = None
        except Exception as e:
            logger.warning(f"Security manager initialization failed: {e}")
            fundaments["security"] = None
    else:
        logger.info("Insufficient services for Security manager, skipping initialization.")
        fundaments["security"] = None
    
    # Log what was actually initialized
    initialized_services = [k for k, v in fundaments.items() if v is not None]
    logger.info(f"Successfully initialized services: {', '.join(initialized_services)}")
    
    return fundaments

async def main():
    """
    The main asynchronous function of the application.
    """
    logger.info("Starting main.py...")
    
    fundaments = await initialize_fundaments()
    
    try:
        # Load the actual app logic here.
        # This is where your 'app/app.py' would be imported and run.
        logger.info("Fundament modules are ready for the app logic.")
        # Example:
        from app.app import start_application
        await start_application(fundaments)
    
    finally:
        # Ensure the database pool is closed gracefully on exit (if it was initialized)
        if fundaments.get("db") is not None:
            await close_db_pool()
            logger.info("Database pool closed.")
        logger.info("Application shut down.")

if __name__ == "__main__":
    asyncio.run(main())
