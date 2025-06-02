from json import loads
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import logging
from typing import Dict, Optional, Any, Union
from http.client import HTTPResponse

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

url_stations = "https://booking.roadsurfer.com/api/en/rally/stations"
url_timeframes = "https://booking.roadsurfer.com/api/en/rally/timeframes"
url_directions = "https://www.google.com/maps/dir"

base_headers = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-UK,en;q=0.7",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "Referer": "https://booking.roadsurfer.com/en/rally?currency=EUR",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "X-Requested-Alias": "rally.startStations"
}

def validate_station_response(data: Dict) -> bool:
    """Validate that the station response has all required fields"""
    if not isinstance(data, dict):
        return False
    required_fields = ["id", "name", "address"]
    return all(field in data for field in required_fields)

def validate_timeframes_response(data: list) -> bool:
    """Validate that the timeframes response has the correct format"""
    if not isinstance(data, list):
        return False
    for timeframe in data:
        if not isinstance(timeframe, dict):
            return False
        if "startDate" not in timeframe or "endDate" not in timeframe:
            return False
    return True

def get_json_from_url(url: str, headers: dict) -> Optional[Union[Dict, list]]:
    """Get JSON data from URL with error handling and validation"""
    req = Request(url, headers=headers)
    response: Optional[HTTPResponse] = None

    try:
        response = urlopen(req)
        if response.status != 200:
            logger.error(f"HTTP Error: Status {response.status} for URL {url}")
            return None

        raw_data = response.read().decode()
        return loads(raw_data)

    except HTTPError as e:
        logger.error(f"HTTP Error {e.code} for URL {url}: {e.reason}")
        return None
    except URLError as e:
        logger.error(f"URL Error for {url}: {e.reason}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error accessing {url}: {str(e)}")
        return None
    finally:
        if response:
            response.close()

def get_station_data(station_id: Optional[int]) -> Optional[Dict]:
    """Get data for a specific station or all stations"""
    try:
        headers = base_headers.copy()
        if station_id is not None:
            url = f"{url_stations}/{station_id}"
            headers.update({"X-Requested-Alias": "rally.fetchRoutes"})
        else:
            url = url_stations
            headers.update({"X-Requested-Alias": "rally.startStations"})

        data = get_json_from_url(url, headers)
        
        if not data:
            logger.error(f"No data received for station_id: {station_id}")
            return None

        # For single station request
        if station_id is not None:
            if not validate_station_response(data):
                logger.error(f"Invalid station data format for ID {station_id}: {data}")
                return None
            return data

        # For all stations request
        if not isinstance(data, list):
            logger.error(f"Invalid stations list format. Got type: {type(data)}")
            return None

        # Filter out invalid station data
        valid_stations = []
        for station in data:
            if validate_station_response(station):
                valid_stations.append(station)
            else:
                logger.warning(f"Invalid station data format: {station}")

        logger.info(f"Found {len(valid_stations)} valid stations out of {len(data)} total")
        return valid_stations

    except Exception as e:
        logger.error(f"Error in get_station_data: {e}")
        return None

def get_stations_data() -> list:
    """Get data for all stations"""
    try:
        stations = get_station_data(None)
        if not stations:
            logger.error("Failed to get stations data")
            return []
        return stations
    except Exception as e:
        logger.error(f"Error in get_stations_data: {e}")
        return []

def get_station_transfer_dates(origin_station_id: int, destination_station_id: int) -> list:
    """Get transfer dates between two stations"""
    try:
        url = f"{url_timeframes}/{origin_station_id}-{destination_station_id}"
        headers = {**base_headers, **{"X-Requested-Alias": "rally.timeframes"}}
        
        data = get_json_from_url(url, headers)
        if not data:
            logger.error(f"No transfer dates found for route {origin_station_id} -> {destination_station_id}")
            return []

        if not validate_timeframes_response(data):
            logger.error(f"Invalid timeframes format for route {origin_station_id} -> {destination_station_id}")
            return []

        return data

    except Exception as e:
        logger.error(f"Error getting transfer dates: {e}")
        return []