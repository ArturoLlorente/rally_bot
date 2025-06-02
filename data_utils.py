import json
from api_utils import get_station_data, get_station_transfer_dates
from datetime import datetime
from tqdm import tqdm
import logging
from typing import Dict, List, Optional, Set

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

stations_data: Dict[int, Dict] = {}
stations_with_returns: List[Dict] = []
output_data: List[Dict] = []

def validate_station_data(station: dict) -> bool:
    """Validate that a station has all required fields"""
    if not isinstance(station, dict):
        logger.error(f"Invalid station data type: {type(station)}")
        return False
    required_fields = ["id", "name", "address"]
    missing_fields = [field for field in required_fields if field not in station]
    if missing_fields:
        logger.error(f"Missing required fields in station data: {missing_fields}")
        return False
    return True

def print_routes_for_stations(stations: list) -> None:
    """Process and print routes for all stations"""
    global output_data
    output_data = []  # Reset output data
    
    if not stations:
        logger.warning("No stations provided to process")
        return

    try:
        for station in stations:
            if not validate_station_data(station):
                logger.warning(f"Invalid station data format: {station}")
                continue
            output_data = process_station_destinations(station)
    except Exception as e:
        logger.error(f"Error processing routes for stations: {e}")
        raise
    return output_data

def process_station_destinations(station: dict) -> None:
    """Process destinations for a single station"""
    try:
        station_id = station.get("id")
        if not station_id:
            logger.error("Station missing ID")
            return

        if station_id not in stations_data:
            logger.warning(f"Station ID {station_id} not found in stations_data")
            return
            
        origin_name = stations_data[station_id].get("name")
        origin_address = station.get("address")
        
        if not origin_name or not origin_address:
            logger.warning(f"Missing origin data for station {station_id}")
            return

        station_output = {
            "origin": origin_name,
            "origin_address": origin_address,
            "returns": []
        }

        if not station.get("returns"):
            logger.debug(f"No returns found for station {station_id}")
            return

        for return_station_id in station["returns"]:
            if return_station_id not in stations_data:
                logger.warning(f"Return station ID {return_station_id} not found in stations_data")
                continue
                
            return_name = stations_data[return_station_id].get("name")
            destination_address = stations_data[return_station_id].get("address")
            
            if not return_name or not destination_address:
                logger.warning(f"Missing return data for station {return_station_id}")
                continue

            try:
                available_dates = get_station_transfer_dates(station_id, return_station_id)
                if not available_dates:
                    logger.debug(f"No available dates for route {station_id} -> {return_station_id}")
                    continue

                dates_output = []
                for date in available_dates:
                    if not isinstance(date, dict) or "startDate" not in date or "endDate" not in date:
                        logger.warning(f"Invalid date format: {date}")
                        continue
                        
                    try:
                        start_date = datetime.strptime(date["startDate"][:10], "%Y-%m-%d").strftime("%d/%m/%Y")
                        end_date = datetime.strptime(date["endDate"][:10], "%Y-%m-%d").strftime("%d/%m/%Y")
                        dates_output.append({"startDate": start_date, "endDate": end_date})
                    except ValueError as e:
                        logger.warning(f"Error parsing dates: {e}")
                        continue

                if dates_output:  # Only add if there are valid dates
                    station_output["returns"].append({
                        "destination": return_name,
                        "destination_address": destination_address,
                        "available_dates": dates_output
                    })
            except Exception as e:
                logger.error(f"Error processing transfer dates: {e}")
                continue

        if station_output["returns"]:  # Only add if there are valid returns
            output_data.append(station_output)
            
        return output_data

    except Exception as e:
        logger.error(f"Error processing station destinations: {e}")
        return output_data

async def get_stations_with_returns(stations: list, progress_callback=None) -> list:
    """Get stations with return routes (async version)"""
    if not stations:
        logger.warning("No stations provided")
        return []

    stations_with_returns = []
    total = len(stations)
    logger.info(f"Processing {total} stations")

    try:
        for i, station in enumerate(stations):
            
            if not validate_station_data(station):
                logger.warning(f"Invalid station data format: {station}")
                continue
                
            station_id = station["id"]
            stations_data[station_id] = station
            
            station_data = get_station_data(station_id)

            if not station_data:
                logger.warning(f"No data received for station {station_id}")
                continue

            if not isinstance(station_data, dict):
                logger.error(f"Invalid station data type: {type(station_data)}")
                continue

            returns = station_data.get("returns")
            if not returns:
                logger.debug(f"No returns for station {station_id}")
                continue
                
            stations_with_returns.append(station_data)

            if progress_callback:
                percent = int((i + 1) / total * 100)
                await progress_callback(percent)

        logger.info(f"Successfully processed {len(stations_with_returns)} stations with returns")
        return stations_with_returns
    except Exception as e:
        logger.error(f"Error getting stations with returns: {e}", exc_info=True)
        return []

def get_stations_with_returns_local(stations: list) -> list:
    """Get stations with return routes (local version)"""
    if not stations:
        logger.warning("No stations provided")
        return []

    stations_with_returns = []

    try:
        for station in tqdm(stations):
            if not validate_station_data(station):
                logger.warning(f"Invalid station data format: {station}")
                continue
                
            stations_data[station["id"]] = station
            station_data = get_station_data(station["id"])

            if not station_data:
                logger.warning(f"No data received for station {station['id']}")
                continue

            if not station_data.get("returns"):
                logger.debug(f"No returns for station {station['id']}")
                continue
                
            stations_with_returns.append(station_data)

        return stations_with_returns
    except Exception as e:
        logger.error(f"Error getting stations with returns: {e}")
        return []

def save_output_to_json(output_data, file_path="station_routes.json") -> None:
    """Save processed data to JSON file"""
    try:
        if not output_data:
            logger.warning("No data to save")
            return

        with open(file_path, "w", encoding='utf-8') as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)
        logger.info(f"Successfully saved data to {file_path}")
    except Exception as e:
        logger.error(f"Error saving output to JSON: {e}")
        raise