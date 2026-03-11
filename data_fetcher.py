from datetime import datetime, timedelta
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
            self.logger.error(f"Invalid station data type: {type(station)}")
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

    # ------------------------------------------------------------------
    # Synchronous update pipeline
    # Designed to be executed inside a ThreadPoolExecutor so that all
    # blocking network calls and time.sleep() calls happen OFF the asyncio
    # event loop, keeping the Telegram bot fully responsive.
    # ------------------------------------------------------------------

    def sync_full_update(self, progress_callback=None, route_callback=None) -> List[Dict]:
        """Run the full station-data update pipeline synchronously.

        Intended to be called from a worker thread via
        ``loop.run_in_executor(executor, ...)``.

        Args:
            progress_callback: Optional *synchronous* callable(percent: int)
                               invoked after each station is fetched.
            route_callback:    Optional *synchronous* callable(route_data: dict)
                               invoked each time a new route is discovered.
                               Use ``functools.partial`` or a closure to forward
                               results back to the asyncio loop via
                               ``asyncio.run_coroutine_threadsafe``.
        Returns:
            List of processed station/route records (same format as
            ``output_data``).
        """
        # ---- Phase 1: fetch the list of valid stations ---------------
        self.get_stations_data()

        if not self.valid_stations:
            self.logger.warning("sync_full_update: no valid stations found")
            return []

        # ---- Phase 2: fetch per-station route lists ------------------
        self.stations_with_returns = []
        total = len(self.valid_stations)
        self.logger.info(f"sync_full_update: fetching {total} stations")

        with tqdm(total=total, desc="Fetching station routes", unit="station") as pbar:
            for i, station in enumerate(self.valid_stations):
                if not self.validate_station_data(station):
                    pbar.update(1)
                    continue

                self.stations_data[station["id"]] = station
                pbar.set_postfix_str(f"Current: {station.get('name', '')[:30]}")

                station_data = self.get_station_data(station["id"])
                self.stations_with_returns.append(station_data)

                if progress_callback:
                    try:
                        progress_callback(int((i + 1) / total * 100))
                    except Exception:
                        pass

                pbar.update(1)

        # ---- Phase 3: resolve destinations / dates / camper data -----
        self.output_data = []

        with tqdm(total=len(self.stations_with_returns), desc="Processing stations", unit="station") as pbar:
            for station in self.stations_with_returns:
                if self.validate_station_data(station):
                    station_name = self.stations_data.get(
                        station.get("id"), {}
                    ).get("name", "Unknown")
                    pbar.set_postfix_str(f"Current: {station_name[:30]}")
                    self._sync_process_station_destinations(station, route_callback=route_callback)
                pbar.update(1)

        self.logger.info(
            f"sync_full_update: finished — {len(self.output_data)} stations with routes"
        )
        return self.output_data

    def _sync_process_station_destinations(self, station: dict, route_callback=None) -> bool:
        """Synchronous counterpart of ``process_station_destinations``.

        Calls *route_callback* synchronously (no ``await``).  When used from
        a worker thread, the caller is responsible for bridging the result
        back to the asyncio event loop.
        """
        try:
            station_id = station.get("id")

            if station_id not in self.stations_data:
                self.logger.warning(f"Station ID {station_id} not found in stations_data")
                return False

            origin_name = self.cleanup_special_characters(
                self.stations_data[station_id].get("name")
            )
            origin_address = self.cleanup_special_characters(
                self.stations_data[station_id].get("address")
            )

            if not origin_name or not origin_address:
                return False

            station_output = {
                "origin": origin_name,
                "origin_address": origin_address,
                "returns": [],
            }

            if not station.get("returns"):
                return False

            for return_station_id in tqdm(
                station["returns"],
                desc=f"  Routes from {origin_name[:20]}",
                unit="route",
                leave=False,
            ):
                if return_station_id not in self.stations_data:
                    continue

                return_name = self.stations_data[return_station_id].get("name")
                destination_address = self.stations_data[return_station_id].get("address")

                if not return_name or not destination_address:
                    continue

                return_name = self.cleanup_special_characters(return_name)
                destination_address = self.cleanup_special_characters(destination_address)

                available_dates = self.get_station_transfer_dates(station_id, return_station_id)
                if not available_dates:
                    continue

                camper_data = self.get_booking_data(station_id, return_station_id, available_dates)
                if not camper_data:
                    continue

                dates_output = []
                first_start_date = None
                first_end_date = None

                for date in available_dates:
                    try:
                        start_date = datetime.strptime(date["startDate"][:10], "%Y-%m-%d")
                        end_date = datetime.strptime(date["endDate"][:10], "%Y-%m-%d")
                        dates_output.append({
                            "startDate": start_date.strftime("%d/%m/%Y"),
                            "endDate":   end_date.strftime("%d/%m/%Y"),
                        })
                        if first_start_date is None:
                            first_start_date = start_date
                            first_end_date = end_date
                    except ValueError:
                        continue

                model_name = "Unknown"
                model_image = ""
                if camper_data:
                    try:
                        camper = camper_data[0]
                        model_name = camper.get("model", {}).get("name", "Unknown")
                        images = camper.get("model", {}).get("images", [])
                        if images:
                            image_url = images[0].get("image", {}).get("url", "")
                            if image_url:
                                model_image = self.download_image(image_url)
                    except Exception as e:
                        self.logger.warning(f"Error extracting camper data: {e}")

                if dates_output and first_start_date and first_end_date:
                    route_data = {
                        "destination": return_name,
                        "destination_address": destination_address,
                        "available_dates": dates_output,
                        "model_name": model_name,
                        "model_image": model_image,
                        "roadsurfer_url": (
                            f"https://booking.roadsurfer.com/en/rally/pick"
                            f"?station={station_id}&endStation={return_station_id}"
                            f"&pickup_date={first_start_date.strftime('%Y-%m-%d')}"
                            f"&return_date={first_end_date.strftime('%Y-%m-%d')}&currency=EUR"
                        ),
                    }
                    station_output["returns"].append(route_data)

                    if route_callback:
                        single_route = {
                            "origin": origin_name,
                            "origin_address": origin_address,
                            "returns": [route_data],
                        }
                        try:
                            route_callback(single_route)
                        except Exception as e:
                            self.logger.error(f"Error in sync route_callback: {e}", exc_info=True)

            self.output_data.append(station_output)
            return True

        except Exception as e:
            self.logger.error(f"_sync_process_station_destinations error: {e}")
            return False


class ImoovaDataFetcher:
    """Class to fetch and process relocation data from the Imoova GraphQL API"""

    GRAPHQL_URL = "https://api.imoova.com/graphql"
    PAGE_SIZE = 100
    REGIONS = ["EU"]

    RELOCATIONS_QUERY = """
    query($regions: [Region!], $first: Int!, $page: Int!, $status: [RelocationStatus!]) {
        relocations(regions: $regions, first: $first, page: $page, status: $status) {
            data {
                id
                departureCity { name state }
                departureOffice { name address { city state country postcode } }
                deliveryCity { name state }
                deliveryOffice { name address { city state country postcode } }
                earliest_departure_date
                latest_departure_date
                images { url }
                vehicle { name type images { url } }
                trip { duration distance }
                hire_unit_rate
                hire_unit_type
                extra_hire_unit_rate
                extra_hire_units_allowed
                currency
                status
            }
            paginatorInfo { total currentPage lastPage hasMorePages }
        }
    }
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.output_data: List[Dict] = []

        # Rate limiting settings
        self.request_delay = 0.1
        self.max_retries = 3
        self.retry_delay = 5

    def _graphql_request(self, query: str, variables: dict) -> Optional[Dict]:
        """Execute a GraphQL request with retry logic"""
        for attempt in range(self.max_retries):
            try:
                if attempt > 0:
                    wait_time = self.retry_delay * (2 ** (attempt - 1))
                    self.logger.info(f"Retrying imoova request (attempt {attempt + 1}/{self.max_retries}) after {wait_time}s")
                    time.sleep(wait_time)
                else:
                    time.sleep(self.request_delay)

                resp = requests.post(
                    self.GRAPHQL_URL,
                    json={"query": query, "variables": variables},
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                result = resp.json()

                if "errors" in result:
                    self.logger.error(f"GraphQL errors: {result['errors']}")
                    return None

                return result.get("data")

            except requests.exceptions.HTTPError as e:
                if resp.status_code == 429 and attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)
                    self.logger.warning(f"Imoova rate limit (429). Waiting {wait_time}s")
                    time.sleep(wait_time)
                    continue
                self.logger.error(f"Imoova HTTP Error {resp.status_code}: {e}")
                return None
            except Exception as e:
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)
                    self.logger.warning(f"Imoova request error: {e}. Retrying in {wait_time}s")
                    time.sleep(wait_time)
                    continue
                self.logger.error(f"Imoova request failed after {self.max_retries} attempts: {e}")
                return None
        return None

    def _fetch_all_relocations(self, progress_callback=None) -> List[Dict]:
        """Fetch all READY relocations from imoova, paginating through results"""
        all_relocations = []
        page = 1
        total_pages = None

        while True:
            variables = {
                "regions": self.REGIONS,
                "first": self.PAGE_SIZE,
                "page": page,
                "status": ["READY"],
            }
            data = self._graphql_request(self.RELOCATIONS_QUERY, variables)
            if not data:
                self.logger.error(f"Failed to fetch imoova relocations page {page}")
                break

            relocations_data = data.get("relocations", {})
            page_items = relocations_data.get("data", [])
            paginator = relocations_data.get("paginatorInfo", {})

            if total_pages is None:
                total_pages = paginator.get("lastPage", 1)
                total = paginator.get("total", 0)
                self.logger.info(f"Imoova: {total} READY relocations across {total_pages} pages")

            all_relocations.extend(page_items)

            if progress_callback and total_pages:
                progress_callback(int(page / total_pages * 100))

            if not paginator.get("hasMorePages", False):
                break

            page += 1

        self.logger.info(f"Imoova: fetched {len(all_relocations)} relocations")
        return all_relocations

    def _group_relocations(self, relocations: List[Dict]) -> List[Dict]:
        """Group relocations by origin city and build output in station_routes format.

        Within each origin, relocations are further grouped by
        (destination city, vehicle name) so that multiple date windows
        for the same route & vehicle share a single return entry.
        """
        # origin_city -> { (dest_city, vehicle_name) -> { ... } }
        origins: Dict[str, Dict] = {}

        for rel in relocations:
            try:
                dep_city = rel.get("departureCity", {}).get("name", "")
                dep_office = rel.get("departureOffice", {}) or {}
                dep_addr = dep_office.get("address", {}) or {}
                origin_address = ", ".join(filter(None, [
                    dep_addr.get("city", ""),
                    dep_addr.get("postcode", ""),
                    dep_addr.get("country", ""),
                ]))

                del_city = rel.get("deliveryCity", {}).get("name", "")
                del_office = rel.get("deliveryOffice", {}) or {}
                del_addr = del_office.get("address", {}) or {}
                dest_address = ", ".join(filter(None, [
                    del_addr.get("city", ""),
                    del_addr.get("postcode", ""),
                    del_addr.get("country", ""),
                ]))

                vehicle = rel.get("vehicle", {}) or {}
                vehicle_name = vehicle.get("name", "Unknown")
                images = vehicle.get("images", []) or []
                if not images:
                    images = rel.get("images", []) or []
                image_url = images[0].get("url", "") if images else ""

                earliest = rel.get("earliest_departure_date", "")
                latest = rel.get("latest_departure_date", "")
                trip = rel.get("trip", {}) or {}
                duration = trip.get("duration", 0)
                extra_nights = rel.get("extra_hire_units_allowed", 0) or 0
                hire_unit_type = rel.get("hire_unit_type", "NIGHT")
                unit_label = "nights" if hire_unit_type == "NIGHT" else "days"
                if extra_nights:
                    duration_str = f"{duration}+{extra_nights} {unit_label}"
                else:
                    duration_str = f"{duration} {unit_label}"
                rate_cents = rel.get("hire_unit_rate", 0) or 0
                extra_rate_cents = rel.get("extra_hire_unit_rate", 0) or 0
                currency = rel.get("currency", "EUR")
                rate = rate_cents / 100
                extra_rate = extra_rate_cents / 100
                rel_id = rel.get("id", "")

                if not dep_city or not del_city or not earliest:
                    continue

                # Compute date window
                try:
                    start_dt = datetime.strptime(earliest, "%Y-%m-%d")
                    if latest:
                        latest_dt = datetime.strptime(latest, "%Y-%m-%d")
                    else:
                        latest_dt = start_dt
                    end_dt = latest_dt + timedelta(days=duration)
                except ValueError:
                    continue

                origin_key = dep_city
                if origin_key not in origins:
                    origins[origin_key] = {
                        "origin": StationDataFetcher.cleanup_special_characters(dep_city),
                        "origin_address": StationDataFetcher.cleanup_special_characters(origin_address),
                        "returns_map": {},
                    }

                route_key = (del_city, vehicle_name)
                rmap = origins[origin_key]["returns_map"]
                if route_key not in rmap:
                    rmap[route_key] = {
                        "destination": StationDataFetcher.cleanup_special_characters(del_city),
                        "destination_address": StationDataFetcher.cleanup_special_characters(dest_address),
                        "available_dates": [],
                        "model_name": vehicle_name,
                        "model_image": "",
                        "image_url": image_url,
                        "imoova_url": f"https://www.imoova.com/en/relocations/{rel_id}",
                        "relocation_ids": [],
                    }
                entry = rmap[route_key]
                entry["available_dates"].append({
                    "startDate": start_dt.strftime("%d/%m/%Y"),
                    "endDate": end_dt.strftime("%d/%m/%Y"),
                    "duration": duration_str,
                    "rate": rate,
                    "extra_rate": extra_rate,
                    "currency": currency,
                })
                entry["relocation_ids"].append(rel_id)

            except Exception as e:
                self.logger.warning(f"Error processing imoova relocation {rel.get('id', '?')}: {e}")
                continue

        # Build final output
        output = []
        for origin_data in origins.values():
            station_obj = {
                "origin": origin_data["origin"],
                "origin_address": origin_data["origin_address"],
                "returns": [],
            }
            for route_entry in origin_data["returns_map"].values():
                station_obj["returns"].append({
                    "destination": route_entry["destination"],
                    "destination_address": route_entry["destination_address"],
                    "available_dates": route_entry["available_dates"],
                    "model_name": route_entry["model_name"],
                    "model_image": route_entry["model_image"],
                    "image_url": route_entry["image_url"],
                    "roadsurfer_url": route_entry["imoova_url"],
                })
            output.append(station_obj)

        return output

    def _download_images(self, output_data: List[Dict]) -> None:
        """Download vehicle images for all routes"""
        for station in output_data:
            for ret in station.get("returns", []):
                image_url = ret.pop("image_url", "")
                if image_url and not ret.get("model_image"):
                    ret["model_image"] = self._download_image(image_url)

    @staticmethod
    def _to_jpeg_url(image_url: str) -> str:
        """Convert an imoova CDN image URL to its JPEG conversion variant.

        Original: https://d3ked445tp4xeq.cloudfront.net/258021/filename.avif
        Converted: https://d3ked445tp4xeq.cloudfront.net/258021/conversions/filename-SMALL.jpg
        """
        parts = image_url.rsplit("/", 1)
        if len(parts) != 2:
            return image_url
        base, filename = parts
        name = filename.rsplit(".", 1)[0] if "." in filename else filename
        return f"{base}/conversions/{name}-SMALL.jpg"

    def _download_image(self, image_url: str) -> str:
        """Download a single image and return the local filename"""
        if not image_url:
            return ""
        try:
            # Convert to JPEG variant so Telegram can display it
            jpeg_url = self._to_jpeg_url(image_url)
            filename = jpeg_url.split("/")[-1]
            filepath = os.path.join("assets", filename)
            if os.path.exists(filepath):
                return filename
            response = requests.get(jpeg_url, stream=True, timeout=15)
            if response.status_code == 200:
                with open(filepath, "wb") as f:
                    for chunk in response.iter_content(1024):
                        f.write(chunk)
                return filename
            else:
                self.logger.warning(f"Failed to download imoova image: {response.status_code} from {jpeg_url}")
                return ""
        except Exception as e:
            self.logger.warning(f"Error downloading imoova image {image_url}: {e}")
            return ""

    def sync_full_update(self, progress_callback=None, route_callback=None) -> List[Dict]:
        """Run the full imoova update pipeline synchronously.

        Designed to be called from a worker thread via run_in_executor.

        Args:
            progress_callback: Optional sync callable(percent: int)
            route_callback:    Optional sync callable(route_data: dict)

        Returns:
            List of station/route records in the same format as
            StationDataFetcher.output_data.
        """
        self.logger.info("Imoova: starting full update")

        # Phase 1: Fetch all relocations
        relocations = self._fetch_all_relocations(progress_callback=progress_callback)
        if not relocations:
            self.logger.warning("Imoova: no relocations found")
            return []

        # Phase 2: Group into station_routes format
        self.output_data = self._group_relocations(relocations)
        self.logger.info(f"Imoova: grouped into {len(self.output_data)} origin stations")

        # Phase 3: Download images
        self._download_images(self.output_data)

        # Phase 4: Fire route callbacks
        if route_callback:
            for station in self.output_data:
                for ret in station.get("returns", []):
                    single_route = {
                        "origin": station["origin"],
                        "origin_address": station["origin_address"],
                        "returns": [ret],
                    }
                    try:
                        route_callback(single_route)
                    except Exception as e:
                        self.logger.error(f"Error in imoova route callback: {e}", exc_info=True)

        self.logger.info(f"Imoova: update complete — {len(self.output_data)} stations")
        return self.output_data


class IndieCampersDataFetcher:
    """Class to fetch and process relocation deal data from the Indie Campers API"""

    SEARCH_URL = "https://edge.indiecampers.com/api/v3/deals/search"
    PAGE_SIZE = 25
    BASE_DEALS_URL = "https://indiecampers.com/deals"

    # Slug → display name mapping for locations.
    # Slugs follow the pattern "city-name" or "city-name-offers".
    # We strip the "-offers" suffix and title-case the remainder.
    # Special cases are handled explicitly.
    _LOCATION_OVERRIDES: Dict[str, str] = {
        "rome-fco": "Rome",
        "rome-fco-offers": "Rome",
        "paris-charles-de-gaulle": "Paris CDG",
        "paris-charles-de-gaulle-offers": "Paris CDG",
        "paris-orly": "Paris Orly",
        "paris-orly-offers": "Paris Orly",
        "london-heathrow": "London Heathrow",
        "london-heathrow-offers": "London Heathrow",
        "milan-malpensa": "Milan Malpensa",
        "milan-malpensa-offers": "Milan Malpensa",
        "brussels-zaventem": "Brussels",
        "coruna": "A Coruña",
        "malmo": "Malmö",
        "munich-offers": "Munich",
    }

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.output_data: List[Dict] = []

        self.request_delay = 2  # seconds between pages (rate-limit safe)
        self.max_retries = 3
        self.retry_delay = 5

        self._session_headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://indiecampers.com/deals",
        }

    # ---- helpers ----

    @classmethod
    def _slug_to_display(cls, slug: str) -> str:
        if slug in cls._LOCATION_OVERRIDES:
            return cls._LOCATION_OVERRIDES[slug]
        clean = slug.removesuffix("-offers")
        return clean.replace("-", " ").title()

    @staticmethod
    def _van_category_to_display(slug: str) -> str:
        """eu-comfort-space-4-auto-select → Comfort Space"""
        parts = slug.split("-")
        # Skip region prefix (eu/na) and trailing spec tokens
        spec_tokens = {
            "auto", "manual", "base", "select",
            "2", "3", "4", "5", "6", "7",
        }
        name_parts = []
        for p in parts[1:]:  # skip region
            if p.lower() in spec_tokens:
                break
            name_parts.append(p.title())
        return " ".join(name_parts) if name_parts else slug

    @staticmethod
    def _build_booking_url(hash_id: str, start_date: str, end_date: str) -> str:
        """Build a deal URL using: https://indiecampers.com/deals/europe/{hash_id}?start=...&end=..."""
        return (
            f"https://indiecampers.com/deals/europe/{hash_id}"
            f"?start={start_date}&end={end_date}"
        )

    # ---- API calls ----

    def _fetch_page(self, page: int) -> Optional[Dict]:
        for attempt in range(self.max_retries):
            try:
                if attempt > 0:
                    wait = self.retry_delay * (2 ** (attempt - 1))
                    self.logger.info(f"IndieCampers: retrying page {page} (attempt {attempt+1}) after {wait}s")
                    time.sleep(wait)

                resp = requests.get(
                    self.SEARCH_URL,
                    params={"page": page},
                    headers=self._session_headers,
                    timeout=15,
                )

                if resp.status_code == 429 and attempt < self.max_retries - 1:
                    wait = self.retry_delay * (2 ** attempt)
                    self.logger.warning(f"IndieCampers: rate-limited (429). Waiting {wait}s")
                    time.sleep(wait)
                    continue

                if resp.status_code == 403 and attempt < self.max_retries - 1:
                    wait = self.retry_delay * (2 ** attempt)
                    self.logger.warning(f"IndieCampers: 403, waiting {wait}s")
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp.json()

            except Exception as e:
                if attempt < self.max_retries - 1:
                    wait = self.retry_delay * (2 ** attempt)
                    self.logger.warning(f"IndieCampers page {page} error: {e}. Retrying in {wait}s")
                    time.sleep(wait)
                    continue
                self.logger.error(f"IndieCampers page {page} failed after {self.max_retries} attempts: {e}")
                return None
        return None

    def _fetch_all_deals(self, progress_callback=None) -> List[Dict]:
        """Fetch all deal routes from all pages."""
        all_routes: List[Dict] = []
        page = 1
        total_pages = None

        while True:
            if page > 1:
                time.sleep(self.request_delay)

            data = self._fetch_page(page)
            if not data:
                self.logger.error(f"IndieCampers: failed to fetch page {page}")
                break

            routes = data.get("data", [])
            if not routes:
                break

            if total_pages is None:
                total = data.get("total", 0)
                total_pages = max(1, -(-total // self.PAGE_SIZE))  # ceil division
                self.logger.info(f"IndieCampers: {total} routes across {total_pages} pages")

            all_routes.extend(routes)

            if progress_callback and total_pages:
                progress_callback(int(page / total_pages * 100))

            if page >= (total_pages or page):
                break
            page += 1

        self.logger.info(f"IndieCampers: fetched {len(all_routes)} routes")
        return all_routes

    # ---- grouping ----

    def _group_deals(self, routes: List[Dict]) -> List[Dict]:
        """Group deals into the station_routes format used by the bot.

        Groups by (origin slug, destination slug, van display name) so that
        multiple date windows share a single return entry.
        """
        origins: Dict[str, Dict] = {}

        for route in routes:
            pickup_slug = route.get("pick_up_location", "")
            dropoff_slug = route.get("drop_off_location", "")
            pickup_display = self._slug_to_display(pickup_slug)
            dropoff_display = self._slug_to_display(dropoff_slug)

            for deal in route.get("deals", []):
                van_slug = deal.get("van_category", "")
                van_display = self._van_category_to_display(van_slug)
                price = deal.get("min_price")
                max_nights = deal.get("max_max_nights")

                origin_key = pickup_slug
                if origin_key not in origins:
                    origins[origin_key] = {
                        "origin": pickup_display,
                        "origin_address": "",
                        "returns_map": {},
                    }

                route_key = (dropoff_slug, van_slug)
                rmap = origins[origin_key]["returns_map"]
                if route_key not in rmap:
                    rmap[route_key] = {
                        "destination": dropoff_display,
                        "destination_address": "",
                        "available_dates": [],
                        "model_name": van_display,
                        "model_image": "",
                        "booking_url": "",  # will be set from first hash_id
                    }

                entry = rmap[route_key]
                for ad in deal.get("available_dates", []):
                    earliest = ad.get("earliest_checkin_date", "")
                    latest = ad.get("latest_checkout_date", "")
                    hash_id = ad.get("hash_id", "")
                    if not earliest:
                        continue
                    try:
                        start_dt = datetime.strptime(earliest, "%Y-%m-%d")
                        end_dt = datetime.strptime(latest, "%Y-%m-%d") if latest else start_dt
                    except ValueError:
                        continue

                    # Use first hash_id for the route-level booking URL
                    if not entry["booking_url"] and hash_id:
                        entry["booking_url"] = self._build_booking_url(
                            hash_id, earliest, latest or earliest
                        )

                    night_count = ad.get("max_nights", max_nights)
                    date_entry = {
                        "startDate": start_dt.strftime("%d/%m/%Y"),
                        "endDate": end_dt.strftime("%d/%m/%Y"),
                    }
                    if night_count is not None:
                        date_entry["duration"] = f"{night_count} nights max"
                    if price is not None:
                        date_entry["rate"] = price
                        date_entry["currency"] = "EUR"
                    entry["available_dates"].append(date_entry)

        # Build final output
        output = []
        for origin_data in origins.values():
            station_obj = {
                "origin": origin_data["origin"],
                "origin_address": origin_data["origin_address"],
                "returns": [],
            }
            for route_entry in origin_data["returns_map"].values():
                station_obj["returns"].append({
                    "destination": route_entry["destination"],
                    "destination_address": route_entry["destination_address"],
                    "available_dates": route_entry["available_dates"],
                    "model_name": route_entry["model_name"],
                    "model_image": route_entry["model_image"],
                    "roadsurfer_url": route_entry["booking_url"],
                })
            output.append(station_obj)

        return output

    # ---- main update pipeline ----

    def sync_full_update(self, progress_callback=None, route_callback=None) -> List[Dict]:
        """Run the full IndieCampers update pipeline synchronously."""
        self.logger.info("IndieCampers: starting full update")

        routes = self._fetch_all_deals(progress_callback=progress_callback)
        if not routes:
            self.logger.warning("IndieCampers: no deals found")
            return []

        self.output_data = self._group_deals(routes)
        self.logger.info(f"IndieCampers: grouped into {len(self.output_data)} origin stations")

        if route_callback:
            for station in self.output_data:
                for ret in station.get("returns", []):
                    single_route = {
                        "origin": station["origin"],
                        "origin_address": station["origin_address"],
                        "returns": [ret],
                    }
                    try:
                        route_callback(single_route)
                    except Exception as e:
                        self.logger.error(f"Error in IndieCampers route callback: {e}", exc_info=True)

        self.logger.info(f"IndieCampers: update complete — {len(self.output_data)} stations")
        return self.output_data