from datetime import datetime
import logging
import json
from typing import Dict, List, Optional
from json import loads
from urllib.request import Request, urlopen
from urllib.parse import urlencode
import logging
from typing import Dict, Optional, Union
from http.client import HTTPResponse

class StationDataFetcher:
    """Class to fetch and process station data from the roadsurfer API"""

    def __init__(self,
                 logger: Optional[logging.Logger] = None) -> None:
        # Initialize logging
        self.logger = logger or logging.getLogger(__name__)

        self.stations_data: Dict[int, Dict] = {}
        self.stations_with_returns: List[Dict] = []
        self.output_data: List[Dict] = []

        self.url_stations = "https://booking.roadsurfer.com/api/en/rally/stations"
        self.url_timeframes = "https://booking.roadsurfer.com/api/en/rally/timeframes"
        self.url_search = "https://booking.roadsurfer.com/api/es/rally/search"
        
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



    def print_routes_for_stations(self) -> None:
        """Process and print routes for all stations"""
        
        self.output_data = []  # Reset output data
        
        if not self.stations_with_returns:
            self.logger.warning("No stations provided to process")
            return

        try:
            for station in self.stations_with_returns:
                if self.validate_station_data(station):
                    self.process_station_destinations(station)

        except Exception as e:
            self.logger.error(f"Error processing routes for stations: {e}")
            raise
        return self.output_data

    def process_station_destinations(self, station: dict) -> None:
        """Process destinations for a single station"""
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

            for return_station_id in station["returns"]:
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
                camper_data = self.get_booking_data(station_id, return_station_id, available_dates)

                dates_output = []
                for date in available_dates:
                    try:
                        start_date = datetime.strptime(date["startDate"][:10], "%Y-%m-%d")
                        end_date = datetime.strptime(date["endDate"][:10], "%Y-%m-%d")
                        dates_output.append({"startDate": start_date.strftime("%d/%m/%Y"), "endDate": end_date.strftime("%d/%m/%Y")})
                    except ValueError as e:
                        self.logger.warning(f"Error parsing dates: {e}")
                        continue
                    
                for camper in camper_data:
                    model_name = camper["model"]["name"]
                    model_image = camper["model"]["images"][0]['image']["url"].split("/")[-1]

                if dates_output:  # Only add if there are valid dates
                    station_output["returns"].append({
                        "destination": return_name,
                        "destination_address": destination_address,
                        "available_dates": dates_output,
                        "model_name": model_name,
                        "model_image": model_image,
                        "roadsurfer_url": f"https://booking.roadsurfer.com/en/rally/pick?pickup_date={start_date}&return_date={end_date}&currency=EUR&startStation={station_id}&endStation={return_station_id}",
                    })



            self.output_data.append(station_output)
            return True

        except Exception as e:
            self.logger.error(f"Error processing station destinations: {e}")
            return False

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
        """Get JSON data from URL with error handling and validation"""
        req = Request(url, headers=headers)
        response: Optional[HTTPResponse] = None

        try:
            response = urlopen(req)
            if response.status != 200:
                self.logger.error(f"HTTP Error: Status {response.status} for URL {url}")
                return None

            raw_data = response.read().decode()
            return loads(raw_data)
        
        except Exception as e:
            self.logger.error(f"Unexpected error accessing {url}: {str(e)}")
            return None
        finally:
            if response:
                response.close()
            
            
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

        for i, station in enumerate(self.valid_stations):
            
            if not self.validate_station_data(station):
                continue
                
            self.stations_data[station["id"]] = station
            
            station_data = self.get_station_data(station["id"])
            self.stations_with_returns.append(station_data)

            if progress_callback:
                percent = int((i + 1) / total * 100)
                await progress_callback(percent)

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