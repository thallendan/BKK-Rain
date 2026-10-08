from datetime import datetime, timezone
import json
import urllib.request


def fetch_text(url):
    """Utility to fetch raw text from climate data servers."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.read().decode('utf-8')
    except Exception as e:
        print(f'Error fetching from {url}: {e}')
        return None


def parse_enso_nino34():
    """Fetches real-time Niño 3.4 SST anomaly from NOAA CPC."""
    url = 'https://www.cpc.ncep.noaa.gov/data/indices/sstoi.indices'
    text = fetch_text(url)
    if text:
        lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
        if len(lines) > 1:
            last_line = lines[-1].split()
            try:
                # Extract ANOM column for Niño 3.4
                return float(last_line[-1])
            except (ValueError, IndexError):
                pass
    return None  # Explicitly return None if fetch or parsing fails


def parse_mjo_phase():
    """Fetches current MJO phase and amplitude from NOAA CPC."""
    url = 'https://www.cpc.ncep.noaa.gov/products/precip/CWlink/daily_mjo_index/proj_series_1.txt'
    text = fetch_text(url)
    if text:
        lines = [
            l.strip()
            for l in text.strip().split('\n')
            if l.strip() and not l.startswith('P')
        ]
        if lines:
            parts = lines[-1].split()
            try:
                phase = int(parts[5])
                amp = float(parts[6])
                return phase, amp
            except (ValueError, IndexError):
                pass
    return None, None  # Explicitly return None if fetch or parsing fails


def compute_thailand_hydrology(enso_val, iod_val, mjo_phase, mjo_amp):
    """Calculates rainfall vector impacts across major Thai river basins."""
    mjo_forcing_map = {
        1: -10.0,
        2: -5.0,
        3: 15.0,
        4: 35.0,
        5: 28.0,
        6: 5.0,
        7: -15.0,
        8: -20.0,
    }
    base_mjo_pct = mjo_forcing_map.get(mjo_phase, 0.0) * min(mjo_amp, 2.0)
    enso_pct = -20.0 * enso_val
    iod_pct = 15.0 * (iod_val if iod_val is not None else 0.0)

    zones = [
        {
            'id': 'ZONE01',
            'en': 'Chao Phraya Basin & Delta (Bangkok Gate)',
            'basin': 'Central Plains & Greater Bangkok',
            'lat': 14.20,
            'lon': 100.50,
            'mjo_effect': round(base_mjo_pct * 1.1, 1),
            'enso_effect': round(enso_pct, 1),
            'iod_effect': round(iod_pct, 1),
            'risk': (
                'High Flood' if (base_mjo_pct + enso_pct) > 15 else 'Normal'
            ),
            'mechanics': (
                f'MJO Phase {mjo_phase} drives tropical convective moisture'
                ' across the Gulf of Thailand into the Chao Phraya delta.'
            ),
        },
        {
            'id': 'ZONE02',
            'en': 'Ping River Headwaters (Bhumibol Dam)',
            'basin': 'Northern Highlands (Chiang Mai/Tak)',
            'lat': 18.79,
            'lon': 98.98,
            'mjo_effect': round(base_mjo_pct * 0.85, 1),
            'enso_effect': round(enso_pct * 0.95, 1),
            'iod_effect': round(iod_pct, 1),
            'risk': 'Moderate Flood',
            'mechanics': (
                'Orographic lifting along the Tenasserim Range amplifies'
                ' monsoon flows into Bhumibol Reservoir.'
            ),
        },
        {
            'id': 'ZONE03',
            'en': 'Mun-Chi Confluence (Mekong Basin)',
            'basin': 'Northeast Plateau (Ubon Ratchathani)',
            'lat': 15.23,
            'lon': 104.85,
            'mjo_effect': round(base_mjo_pct * 1.2, 1),
            'enso_effect': round(enso_pct * 1.1, 1),
            'iod_effect': round(iod_pct, 1),
            'risk': 'Severe Flood',
            'mechanics': (
                'Monsoon troughing creates strong moisture convergence over'
                ' Eastern Isan.'
            ),
        },
        {
            'id': 'ZONE04',
            'en': 'Andaman Coast & Western Ranges',
            'basin': 'Southern West Coast (Phuket/Ranong)',
            'lat': 8.20,
            'lon': 98.30,
            'mjo_effect': round(base_mjo_pct * 1.35, 1),
            'enso_effect': round(enso_pct * 1.1, 1),
            'iod_effect': round(iod_pct * 1.2, 1),
            'risk': 'High Surge',
            'mechanics': (
                'Direct onshore squall lines triggered by active Indian Ocean'
                ' convective waves.'
            ),
        },
        {
            'id': 'ZONE05',
            'en': 'Eastern Seaboard Coast',
            'basin': 'Rayong & Chonburi Marine',
            'lat': 12.80,
            'lon': 101.25,
            'mjo_effect': round(base_mjo_pct * 0.95, 1),
            'enso_effect': round(enso_pct * 0.9, 1),
            'iod_effect': round(iod_pct, 1),
            'risk': 'Moderate Flood',
            'mechanics': (
                'Moisture convergence along Gulf of Thailand coastal boundaries.'
            ),
        },
        {
            'id': 'ZONE06',
            'en': 'Nan River Catchment (Sirikit Dam)',
            'basin': 'Upper North Catchment (Uttaradit)',
            'lat': 17.62,
            'lon': 100.09,
            'mjo_effect': round(base_mjo_pct * 0.9, 1),
            'enso_effect': round(enso_pct, 1),
            'iod_effect': round(iod_pct, 1),
            'risk': 'Moderate Flood',
            'mechanics': (
                'Monsoonal rainfall contributing directly to northern storage'
                ' reservoirs.'
            ),
        },
    ]
    return zones


def main():
    enso = parse_enso_nino34()
    mjo_phase, mjo_amp = parse_mjo_phase()
    iod = None  # Left as None if live source is unverified

    utc_now = datetime.now(timezone.utc).isoformat()

    # STRICT FAILURE CHECK: If essential telemetry fails to fetch, mark is_live = False
    if enso is None or mjo_phase is None or mjo_amp is None:
        payload = {
            'is_live': False,
            'status': 'FETCH_FAILED',
            'error_message': (
                'Failed to fetch live climate telemetry from NOAA/BoM'
                ' servers. Data has not been updated.'
            ),
            'updated_at_utc': utc_now,
            'teleconnections': None,
            'zones': [],
        }
        print(
            'CRITICAL WARNING: Live data fetch failed. Outputting failure'
            ' state without fallback defaults.'
        )
    else:
        zones = compute_thailand_hydrology(enso, iod, mjo_phase, mjo_amp)
        payload = {
            'is_live': True,
            'status': 'OK',
            'updated_at_utc': utc_now,
            'teleconnections': {
                'enso': enso,
                'iod': iod if iod is not None else 0.0,
                'mjo_phase': mjo_phase,
                'mjo_amplitude': mjo_amp,
            },
            'zones': zones,
        }
        print('SUCCESS: Live telemetry fetched and calculated successfully.')

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)


if __name__ == '__main__':
    main()
