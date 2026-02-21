from datetime import datetime
import os
import logging
import json
import requests
import time
from typing import Dict, List, Optional, Callable, Any
from json import loads
from urllib.request import Request, urlopen
from urllib.parse import urlencode
import logging
from typing import Dict, Optional, Union
from http.client import HTTPResponse
from urllib.error import HTTPError
from tqdm import tqdm

class StationDataFetcher:
    """Class to fetch and process station data from the roadsurfer API"""

    def __init__(self,
                 logger: Optional[logging.Logger] = None) -> None:
        # Initialize logging
        self.logger = logger or logging.getLogger(__name__)

        self.stations_data: Dict[int, Dict] = {}
        self.stations_with_returns: List[Dict] = []
        self.output_data: List[Dict] = []
        self.valid_stations: List[Dict] = []  # Initialize valid stations list

        self.url_stations = "https://booking.roadsurfer.com/api/en/rally/stations"
        self.url_timeframes = "https://booking.roadsurfer.com/api/en/rally/timeframes"
        self.url_search = "https://booking.roadsurfer.com/api/es/rally/search"
        
        # Rate limiting settings
        self.request_delay = 0.1  # Delay between requests in seconds
        self.max_retries = 3  # Maximum number of retries for failed requests
        self.retry_delay = 5  # Initial retry delay in seconds
        
        self.base_headers = {
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

    @staticmethod
    def cleanup_special_characters(address: str) -> str:
        """Remove special characters from address"""
        if not address:
            return address
        # Replace special characters with their ASCII equivalents
        replacements = {
            "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
            "Ä": "Ae", "Ö": "OE", "Ü": "UE",
            "Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ú": "U",
            "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
            "ø": "oe", "Ø": "OE", "‘": "'", "’": "'", "“": '"', "”": '"',
            ",": "", ";": "", ":": "", "!": "", "?": "", ".": "", "-": " ", "_": " ", "/": " ", "\\": " ", "|": " "
        }
            
        for char, replacement in replacements.items():
            address = address.replace(char, replacement)
        address = address.replace("(", "").replace(")", "").replace("'", "").replace('"', "")
        address = address.replace("\t", " ")
        address = " ".join(address.split())
        return address



    async def print_routes_for_stations(self, route_callback: Optional[Callable[[Dict], Any]] = None) -> None:
        """Process and print routes for all stations
        
        Args:
            route_callback: Optional async callback function called when a new route is found.
                           Receives the route data as a dictionary.
        """
        
        self.output_data = []  # Reset output data
        
        if not self.stations_with_returns:
            self.logger.warning("No stations provided to process")
            return

        try:
            # Add progress bar for processing stations
            with tqdm(total=len(self.stations_with_returns), desc="Processing stations", unit="station") as pbar:
                for station in self.stations_with_returns:
                    if self.validate_station_data(station):
                        station_name = self.stations_data.get(station.get("id"), {}).get("name", "Unknown")
                        pbar.set_postfix_str(f"Current: {station_name[:30]}")
                        await self.process_station_destinations(station, route_callback=route_callback)
                    pbar.update(1)

        except Exception as e:
            self.logger.error(f"Error processing routes for stations: {e}")
            raise
        return self.output_data

    async def process_station_destinations(self, station: dict, route_callback: Optional[Callable[[Dict], Any]] = None) -> None:
        """Process destinations for a single station
        
        Args:
            station: Station data dictionary
            route_callback: Optional async callback function called when a new route is found
        """
        try:
            station_id = station.get("id")

            if station_id not in self.stations_data:
                self.logger.warning(f"Station ID {station_id} not found in stations_data")
                return
                
            origin_name = self.cleanup_special_characters(self.stations_data[station_id].get("name"))
            origin_address = self.cleanup_special_characters(self.stations_data[station_id].get("address"))
                
            if not origin_name or not origin_address:
                self.logger.warning(f"Missing origin data for station {station_id}, name: {origin_name}, address: {origin_address}")
                return

            station_output = {
                "origin": origin_name,
                "origin_address": origin_address,
                "returns": []
            }

            if not station.get("returns"):
                #self.logger.warning(f"No returns found for station {station_id}: {origin_name}")
                return

            # Add progress bar for processing return stations
            returns_list = station["returns"]
            for return_station_id in tqdm(returns_list, desc=f"  Routes from {origin_name[:20]}", unit="route", leave=False):
                if return_station_id not in self.stations_data:
                    self.logger.warning(f"Return station ID {return_station_id} not found in stations_data")
                    continue
                    
                return_name = self.stations_data[return_station_id].get("name")
                destination_address = self.stations_data[return_station_id].get("address")
                
                if not return_name or not destination_address:
                    self.logger.warning(f"Missing return data for station {return_station_id}, name: {return_name}, address: {destination_address}")
                    continue
                
                return_name = self.cleanup_special_characters(return_name)
                destination_address = self.cleanup_special_characters(destination_address)

                available_dates = self.get_station_transfer_dates(station_id, return_station_id)
                
                # Skip if no available dates
                if not available_dates:
                    continue
                    
                camper_data = self.get_booking_data(station_id, return_station_id, available_dates)
                
                # Skip if no camper data
                if not camper_data:
                    continue

                dates_output = []
                first_start_date = None
                first_end_date = None
                
                for date in available_dates:
                    try:
                        start_date = datetime.strptime(date["startDate"][:10], "%Y-%m-%d")
                        end_date = datetime.strptime(date["endDate"][:10], "%Y-%m-%d")
                        dates_output.append({"startDate": start_date.strftime("%d/%m/%Y"), "endDate": end_date.strftime("%d/%m/%Y")})
                        
                        # Store first date for URL
                        if first_start_date is None:
                            first_start_date = start_date
                            first_end_date = end_date
                    except ValueError as e:
                        self.logger.warning(f"Error parsing dates: {e}")
                        continue
                
                # Get model info from first camper if available
                model_name = "Unknown"
                model_image = ""
                if camper_data and len(camper_data) > 0:
                    try:
                        camper = camper_data[0]
                        model_name = camper.get("model", {}).get("name", "Unknown")
                        images = camper.get("model", {}).get("images", [])
                        if images and len(images) > 0:
                            image_path = images[0].get('image', {}).get("url", "")
                            if image_path:
                                model_image = self.download_image(image_path)
                    except Exception as e:
                        self.logger.warning(f"Error extracting camper data: {e}")

                if dates_output and first_start_date and first_end_date:  # Only add if there are valid dates
                    route_data = {
                        "destination": return_name,
                        "destination_address": destination_address,
                        "available_dates": dates_output,
                        "model_name": model_name,
                        "model_image": model_image,
                        "roadsurfer_url": f"https://booking.roadsurfer.com/en/rally/pick?station={station_id}&endStation={return_station_id}&pickup_date={first_start_date.strftime('%Y-%m-%d')}&return_date={first_end_date.strftime('%Y-%m-%d')}&currency=EUR",
                    }
                    station_output["returns"].append(route_data)
                    
                    # Call the callback if provided (for real-time notifications)
                    if route_callback:
                        single_route = {
                            'origin': origin_name,
                            'origin_address': origin_address,
                            'returns': [route_data]
                        }
                        try:
                            # Handle both sync and async callbacks
                            import inspect
                            if inspect.iscoroutinefunction(route_callback):
                                await route_callback(single_route)
                            else:
                                # Sync callback
                                route_callback(single_route)
                        except Exception as e:
                            self.logger.error(f"Error in route callback: {e}", exc_info=True)



            self.output_data.append(station_output)
            return True

        except Exception as e:
            self.logger.error(f"Error processing station destinations: {e}")
            return False

    def download_image(self, image_url: str) -> str:

        if image_url:
            filename = image_url.split("/")[-1]
            filepath = os.path.join("assets", filename)

            # Check if file already exists
            if os.path.exists(filepath):
                return filename

            # Download image
            response = requests.get(image_url, stream=True)
            if response.status_code == 200:
                with open(filepath, 'wb') as out_file:
                    for chunk in response.iter_content(1024):
                        out_file.write(chunk)
                return filename
            else:
                print(f"Failed to download image from {image_url}")
                return ""
        return ""

    def get_station_transfer_dates(self, origin_station_id: int, destination_station_id: int) -> list:
        """Get transfer dates between two stations"""
        try:
            url = f"{self.url_timeframes}/{origin_station_id}-{destination_station_id}"
            headers = {**self.base_headers, **{"X-Requested-Alias": "rally.timeframes"}}
            
            data = self.get_json_from_url(url, headers)
            if not data:
                self.logger.error(f"No transfer dates found for route {origin_station_id} -> {destination_station_id}")
                return []

            if not self.validate_timeframes_response(data):
                self.logger.error(f"Invalid timeframes format for route {origin_station_id} -> {destination_station_id}")
                return []

            return data

        except Exception as e:
            self.logger.error(f"Error getting transfer dates: {e}")
            return []
        
    def get_booking_data(self, origin_station_id: int, destination_station_id: int, available_dates: list) -> Optional[Dict]:
        """Get booking data for a specific route"""
        try:
            headers = self.base_headers.copy()
            headers.update({"X-Requested-Alias": "rally.search"})
            
            params = {
                "stations": f"[[{origin_station_id},{destination_station_id}]]",
                "range": f'["{available_dates[0]["startDate"].split("T")[0]}","{available_dates[0]["endDate"].split("T")[0]}"]',
                "currency": "EUR",
                "models": json.dumps([])  # Convertir la lista a string JSON
            }
                
            query_string = urlencode(params)
            url = f"{self.url_search}?{query_string}"
                        
            data = self.get_json_from_url(url, headers)
            if not data:
                self.logger.error(f"No booking data found for route {origin_station_id} -> {destination_station_id}")
                return None

            return data

        except Exception as e:
            self.logger.error(f"Error getting booking data: {e}")
            return None
        
    
    def get_json_from_url(self, url: str, headers: dict) -> Optional[Union[Dict, list]]:
        """Get JSON data from URL with error handling, validation, and retry logic"""
        response: Optional[HTTPResponse] = None
        
        for attempt in range(self.max_retries):
            try:
                # Add delay between requests to avoid rate limiting
                if attempt > 0:
                    # Exponential backoff for retries
                    wait_time = self.retry_delay * (2 ** (attempt - 1))
                    self.logger.info(f"Retrying request to {url} (attempt {attempt + 1}/{self.max_retries}) after {wait_time}s")
                    time.sleep(wait_time)
                else:
                    # Normal delay between requests
                    time.sleep(self.request_delay)
                
                req = Request(url, headers=headers)
                response = urlopen(req)
                
                if response.status != 200:
                    self.logger.error(f"HTTP Error: Status {response.status} for URL {url}")
                    return None

                raw_data = response.read().decode()
                return loads(raw_data)
            
            except HTTPError as e:
                if e.code == 429:  # Too Many Requests
                    if attempt < self.max_retries - 1:
                        wait_time = self.retry_delay * (2 ** attempt)
                        self.logger.warning(f"Rate limit hit (429). Waiting {wait_time}s before retry {attempt + 1}/{self.max_retries}")
                        time.sleep(wait_time)
                        continue
                    else:
                        self.logger.error(f"Rate limit hit (429) after {self.max_retries} attempts for {url}")
                        return None
                else:
                    self.logger.error(f"HTTP Error {e.code} accessing {url}: {str(e)}")
                    return None
            
            except Exception as e:
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)
                    self.logger.warning(f"Error accessing {url}: {str(e)}. Retrying in {wait_time}s")
                    time.sleep(wait_time)
                    continue
                else:
                    self.logger.error(f"Unexpected error accessing {url} after {self.max_retries} attempts: {str(e)}")
                    return None
            finally:
                if response:
                    response.close()
                    response = None
        
        return None
            
            
    def validate_timeframes_response(self, data: list) -> bool:
        """Validate that the timeframes response has the correct format"""
        if not isinstance(data, list):
            return False
        for timeframe in data:
            if not isinstance(timeframe, dict):
                return False
            if "startDate" not in timeframe or "endDate" not in timeframe:
                return False
        return True
    

    def validate_station_data(self, station: dict) -> bool:
        """Validate that a station has all required fields"""
        if not isinstance(station, dict):
            logger.error(f"Invalid station data type: {type(station)}")
            return False
        required_fields = ["id", "name", "address"]
        missing_fields = [field for field in required_fields if field not in station]
        if missing_fields:
            self.logger.error(f"Missing required fields in station data: {missing_fields}")
            return False
        return True

    async def get_stations_with_returns(self, progress_callback=None) -> list:
        """Get stations with return routes (async version)"""
        if not self.valid_stations:
            self.logger.warning("No stations provided")
            return []

        self.stations_with_returns = []
        total = len(self.valid_stations)
        self.logger.info(f"Processing {total} stations")

        # Add progress bar for fetching station routes
        with tqdm(total=total, desc="Fetching station routes", unit="station") as pbar:
            for i, station in enumerate(self.valid_stations):
                
                if not self.validate_station_data(station):
                    pbar.update(1)
                    continue
                    
                self.stations_data[station["id"]] = station
                station_name = station.get("name", "Unknown")
                pbar.set_postfix_str(f"Current: {station_name[:30]}")
                
                station_data = self.get_station_data(station["id"])
                self.stations_with_returns.append(station_data)

                if progress_callback:
                    percent = int((i + 1) / total * 100)
                    await progress_callback(percent)
                
                pbar.update(1)

        self.logger.info(f"Successfully processed {len(self.stations_with_returns)} stations with returns")
        return

    def get_station_data(self, station_id: Optional[int]) -> Optional[Dict]:
        """Get data for a specific station or all stations"""
        try:
            headers = self.base_headers.copy()
            if station_id is not None:
                url = f"{self.url_stations}/{station_id}"
                headers.update({"X-Requested-Alias": "rally.fetchRoutes"})
            else:
                url = self.url_stations
                headers.update({"X-Requested-Alias": "rally.startStations"})

            data = self.get_json_from_url(url, headers)

            # For single station request
            if station_id is not None:
                return data

            # For all stations request
            if not isinstance(data, list):
                self.logger.error(f"Invalid stations list format. Got type: {type(data)}")
                return None

            self.valid_stations = []
            for station in data:
                if self.validate_station_data(station):
                    self.valid_stations.append(station)
                else:
                    self.logger.warning(f"Invalid station data format: {station}, skipping")

            self.logger.info(f"Found {len(self.valid_stations)} valid stations out of {len(data)} total")
            return

        except Exception as e:
            self.logger.error(f"Error in get_station_data: {e}")
            return None

    def get_stations_data(self) -> list:
        """Get data for all stations"""
        try:
            return self.get_station_data(None)
        except Exception as e:
            self.logger.error(f"Error in get_stations_data: {e}")
            return []
        
        
        

    def save_output_to_json(self, file_path="station_routes.json") -> None:
        """Save processed data to JSON file"""
        try:
            if not self.output_data:
                self.logger.warning("No data to save")
                return

            with open(file_path, "w", encoding='utf-8') as f:
                json.dump(self.output_data, f, indent=4, ensure_ascii=False)
            self.logger.info(f"Successfully saved data to {file_path}")
        except Exception as e:
            self.logger.error(f"Error saving output to JSON: {e}")
            raise