from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build


def get_search_console_data(
    property_url: str, days_back: int = 30, row_limit: int = 10
):
    """Ruft die Top-Suchanfragen (Keywords) für eine bestimmte Website-Property ab.

    Args:
        property_url (str): Die URL der Property (z.B. 'sc-domain:example.com' oder
          'https://example.com').
        days_back (int): Für wie viele Tage in der Vergangenheit Daten geladen
          werden sollen.
        row_limit (int): Maximale Anzahl an Zeilen, die zurückgegeben werden.

    Returns:
        list: Eine Liste von Dictionaries mit Keyword, Klicks, Impressionen, CTR
        und Position.
    """
    # 1. Authentifizierung laden
    # Ersetze 'path/to/credentials.json' mit dem Pfad zu deinem Google-Schlüssel
    creds = service_account.Credentials.from_service_account_file(
        "credentials.json",
        scopes=["https://googleapis.com"],
    )

    service = build("webmasters", "v3", credentials=creds)

    # 2. Zeitraum berechnen (GSC-Daten haben meist 2-3 Tage Verzögerung)
    end_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back + 2)).strftime(
        "%Y-%m-%d"
    )

    # 3. API-Abfrage (Request Payload) definieren
    request_body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": [
            "query"
        ],  # Holt die Keywords. Für Seiten nimmst du 'page'
        "rowLimit": row_limit,
    }

    try:
        # 4. API aufrufen
        response = (
            service.searchanalytics()
            .query(siteUrl=property_url, body=request_body)
            .execute()
        )
        rows = response.get("rows", [])

        # 5. Daten für das LLM sauber formatieren
        formatted_data = []
        for row in rows:
            formatted_data.append(
                {
                    "keyword": row["keys"][0],
                    "clicks": row["clicks"],
                    "impressions": row["impressions"],
                    "ctr": round(row["ctr"] * 100, 2),  # in % umrechnen
                    "position": round(row["position"], 1),
                }
            )

        return formatted_data

    except Exception as e:
        return f"Fehler bei der API-Abfrage: {str(e)}"
