# hmmm so müsste es klappen!
# APP_start > backround_check.. write_to>.hub_state.db > most used >psql
# set_calculator -?
import os
import sqlite3
from fundaments import postgresql as db

async def sync_dynamic_pricing_to_neondb(pricing_models_list: list[dict]):
    """
    Nimmt die via API (OpenRouter/LiteLLM) gezogenen Modelle 
    und speichert sie im Bulk in NeonDB + SQLite-Cache.

    """
    if not pricing_models_list:
        return

    # 1. Sicherstellen, dass die Tabelle in NeonDB existiert
    await db.execute_secured_query("""
        CREATE TABLE IF NOT EXISTS public.llm_pricing (
            model_name VARCHAR(255) PRIMARY KEY,
            prompt_cost_per_m NUMERIC(12, 4),
            completion_cost_per_m NUMERIC(12, 4),
            max_tokens_limit INTEGER,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
    """, fetch_method='execute')

    # 2. Bulk-Insert / Upsert direkt in NeonDB (über deine postgresql.py Pool-Verbindung)
    # Da asyncpg keine standardmäßige executemany-Syntax mit %s hat, nutzen wir connection.copy_records_to_table 
    # oder einen optimierten Batch-Insert über den Pool.
    pool = await db.init_db_pool()
    if pool:
        async with pool.acquire() as conn:
            # Daten für den Bulk-Insert vorbereiten
            records = [
                (
                    m.get("model_name"), 
                    float(m.get("prompt_cost", 0.0)), 
                    float(m.get("completion_cost", 0.0)), 
                    int(m.get("max_tokens", 4096))
                ) 
                for m in pricing_models_list
            ]
            
            # Effizienter Upsert mit executemany in asyncpg
            await conn.executemany("""
                INSERT INTO public.llm_pricing (model_name, prompt_cost_per_m, completion_cost_per_m, max_tokens_limit, updated_at)
                VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP)
                ON CONFLICT (model_name) DO UPDATE SET
                    prompt_cost_per_m = EXCLUDED.prompt_cost_per_m,
                    completion_cost_per_m = EXCLUDED.completion_cost_per_m,
                    max_tokens_limit = EXCLUDED.max_tokens_limit,
                    updated_at = CURRENT_TIMESTAMP;
            """, records)

    # 3. Direkt in die lokale SQLite-Temp-Datei (`/tmp/.hub_state.db` auf HF) spiegeln
    sqlite_path = "/tmp/.hub_state.db" if os.getenv("SPACE_ID") else "app/.hub_state.db"
    os.makedirs(os.path.dirname(sqlite_path), exist_ok=True)

    with sqlite3.connect(sqlite_path) as sqlite_conn:
        sqlite_conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_pricing (
                model_name TEXT PRIMARY KEY,
                prompt_cost_per_m REAL,
                completion_cost_per_m REAL,
                max_tokens_limit INTEGER
            )
        """)
        sqlite_conn.executemany("""
            INSERT OR REPLACE INTO llm_pricing (model_name, prompt_cost_per_m, completion_cost_per_m, max_tokens_limit)
            VALUES (?, ?, ?, ?)
        """, [(m.get("model_name"), float(m.get("prompt_cost", 0.0)), float(m.get("completion_cost", 0.0)), int(m.get("max_tokens", 4096))) for m in pricing_models_list])

    print(f"[PRICING_SYNC] {len(pricing_models_list)} Modelle erfolgreich in NeonDB und lokal nach {sqlite_path} geschrieben.")
