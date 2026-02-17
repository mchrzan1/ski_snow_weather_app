import tkinter as tk
from tkinter import ttk, messagebox
import json
import os
import pandas as pd

import openmeteo_requests
import requests_cache
from retry_requests import retry

# -------------------------
# Open-Meteo setup
# -------------------------
cache_session = requests_cache.CachedSession(".cache", expire_after=3600)
retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
openmeteo = openmeteo_requests.Client(session=retry_session)

# -------------------------
# Locations persistence
# -------------------------
LOCATIONS_FILE = "locations.json"

# IMPORTANT: store locations as {"name": {"lat": ..., "lon": ...}}
DEFAULT_LOCATIONS = {
    "Aspen Mountain": {"lat": 39.156895, "lon": -106.820015},
    "Deer Valley": {"lat": 40.619605, "lon": -111.485073},
    "Taos Ski Valley": {"lat": 36.576941, "lon": -105.449794},
}

DEFAULT_SETTINGS = {
    "highlight_snow_enabled": True,
    "highlight_snow_over_cm": 1.0,
    "highlight_rain_enabled": True,
    "highlight_rain_over_mm": 1.0,
}


def load_locations():
    if not os.path.exists(LOCATIONS_FILE):
        data = {"locations": DEFAULT_LOCATIONS, "settings": DEFAULT_SETTINGS}
        with open(LOCATIONS_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return data

    with open(LOCATIONS_FILE, "r") as f:
        data = json.load(f)

    # Backward-compat: if old format {"Name": [lat, lon] or (lat, lon)}, convert it
    # Also backward-compat: if file only had locations dict at top level, migrate to {"locations": ..., "settings": ...}
    if "locations" not in data:
        # data is assumed to be locations dict
        locations = data
        for k, v in list(locations.items()):
            if isinstance(v, (list, tuple)) and len(v) == 2:
                locations[k] = {"lat": float(v[0]), "lon": float(v[1])}
        data = {"locations": locations, "settings": DEFAULT_SETTINGS}
        with open(LOCATIONS_FILE, "w") as f2:
            json.dump(data, f2, indent=2)
        return data

    # If locations are still in old tuple/list form inside the new structure
    for k, v in list(data["locations"].items()):
        if isinstance(v, (list, tuple)) and len(v) == 2:
            data["locations"][k] = {"lat": float(v[0]), "lon": float(v[1])}

    # Ensure settings keys exist
    if "settings" not in data or not isinstance(data["settings"], dict):
        data["settings"] = {}
    for k, v in DEFAULT_SETTINGS.items():
        data["settings"].setdefault(k, v)

    return data


def save_locations(locations):
    # Keep the function name, but now we save both locations + settings in the same persistent file.
    with open(LOCATIONS_FILE, "w") as f:
        json.dump(locations, f, indent=2)


# -------------------------
# Weather logic (DAILY ONLY, possible to add hourly)
# -------------------------
def get_daily_forecast(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": [
            "snowfall_sum",
            "rain_sum",
            "temperature_2m_max",
            "temperature_2m_min",
            "sunshine_duration",
            "weather_code",
        ],
	    "forecast_days": 16,
        "timezone": "America/Denver",
    }

    response = openmeteo.weather_api(url, params=params)[0]
    daily = response.Daily()

    dates = pd.date_range(
        start=pd.to_datetime(daily.Time() + response.UtcOffsetSeconds(), unit="s", utc=True),
        end=pd.to_datetime(daily.TimeEnd() + response.UtcOffsetSeconds(), unit="s", utc=True),
        freq=pd.Timedelta(seconds=daily.Interval()),
        inclusive="left",
    )

    df = pd.DataFrame({
        "Date": dates.tz_localize(None),  # make it naive for easy .date() printing in Tkinter
        "Snowfall (mm)": daily.Variables(0).ValuesAsNumpy(),
        "Rain (mm)": daily.Variables(1).ValuesAsNumpy(),
        "T max (°C)": daily.Variables(2).ValuesAsNumpy(),
        "T min (°C)": daily.Variables(3).ValuesAsNumpy(),
        "Sunshine duration (s)": daily.Variables(4).ValuesAsNumpy(),
        "Weather code": daily.Variables(5).ValuesAsNumpy(),
    })

    return df


# -------------------------
# GUI
# -------------------------
class WeatherApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Daily Weather Forecast")
        self.geometry("800x550")

        state = load_locations()
        self.locations = state["locations"]
        settings = state.get("settings", DEFAULT_SETTINGS)

        self.create_widgets()

                # --- Highlight configuration (defaults) ---
        self.highlight_snow_enabled = tk.BooleanVar(value=settings.get("highlight_snow_enabled", True))
        self.highlight_snow_over_cm = tk.DoubleVar(value=settings.get("highlight_snow_over_cm", 1.0))

        self.highlight_rain_enabled = tk.BooleanVar(value=settings.get("highlight_rain_enabled", True))
        self.highlight_rain_over_mm = tk.DoubleVar(value=settings.get("highlight_rain_over_mm", 1.0))


    def create_widgets(self):
        frame_top = tk.Frame(self)
        frame_top.pack(pady=10)

        tk.Label(frame_top, text="Select location:").pack(side=tk.LEFT, padx=5)

        self.location_var = tk.StringVar()
        self.location_combo = ttk.Combobox(
            frame_top,
            textvariable=self.location_var,
            values=list(self.locations.keys()),
            state="readonly",
            width=30,
        )
        self.location_combo.pack(side=tk.LEFT, padx=5)
        self.location_combo.current(0)

        ttk.Button(frame_top, text="Get forecast", command=self.show_forecast).pack(
            side=tk.LEFT, padx=5
        )

        ttk.Button(frame_top, text="Add location", command=self.add_location_window).pack(
            side=tk.LEFT, padx=5
        )

        ttk.Button(frame_top, text="Configuration", command=self.open_config).pack(
            side=tk.LEFT, padx=5
        )

        self.output = tk.Text(self, height=20, width=100)

                # Highlight tags
        self.output.tag_configure("snow_highlight", background="yellow")
        self.output.tag_configure("rain_highlight", background="red", foreground="white")
        self.output.pack(padx=10, pady=10)

    def persist_state(self):
        state = {
            "locations": self.locations,
            "settings": {
                "highlight_snow_enabled": bool(self.highlight_snow_enabled.get()),
                "highlight_snow_over_cm": float(self.highlight_snow_over_cm.get()),
                "highlight_rain_enabled": bool(self.highlight_rain_enabled.get()),
                "highlight_rain_over_mm": float(self.highlight_rain_over_mm.get()),
            },
        }
        save_locations(state)

    def show_forecast(self):
        location_name = self.location_var.get()
        loc = self.locations[location_name]

        try:
            df = get_daily_forecast(loc["lat"], loc["lon"])
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return

        self.output.delete("1.0", tk.END)
        self.output.insert(tk.END, f"Daily forecast for {location_name}\n")
        self.output.insert(tk.END, "-" * 60 + "\n")

        for _, row in df.iterrows():
                    date_str = str(row["Date"].date())

                    tmin = float(row["T min (°C)"])
                    tmax = float(row["T max (°C)"])

                    rain_mm = float(row["Rain (mm)"])
                    snow_cm = float(row["Snowfall (mm)"])   # Openmeteo issue - it was already in cm even if openmeteo config showed mm

                    sun_h = float(row["Sunshine duration (s)"]) / 3600.0
                    code = int(float(row["Weather code"]))

                    # Insert line in pieces so we can tag-highlight just snow/rain parts
                    self.output.insert(tk.END, f"{date_str} | Min: {tmin:.1f}°C | Max: {tmax:.1f}°C | ")

                    # Rain part (highlight in red if enabled and above threshold)
                    rain_text = f"Rain: {rain_mm:.1f} mm"
                    if self.highlight_rain_enabled.get() and rain_mm > float(self.highlight_rain_over_mm.get()):
                        self.output.insert(tk.END, rain_text, "rain_highlight")
                    else:
                        self.output.insert(tk.END, rain_text)

                    self.output.insert(tk.END, " | ")

                    # Snow part (highlight in yellow if enabled and above threshold)
                    snow_text = f"Snow: {snow_cm:.1f} cm"
                    if self.highlight_snow_enabled.get() and snow_cm > float(self.highlight_snow_over_cm.get()):
                        self.output.insert(tk.END, snow_text, "snow_highlight")
                    else:
                        self.output.insert(tk.END, snow_text)

                    self.output.insert(tk.END, f" | Sun: {sun_h:.1f} h | Code: {code}\n")

    def add_location_window(self):
        win = tk.Toplevel(self)
        win.title("Add location")
        win.geometry("300x200")

        tk.Label(win, text="Location name").pack()
        name_entry = tk.Entry(win)
        name_entry.pack()

        tk.Label(win, text="Latitude").pack()
        lat_entry = tk.Entry(win)
        lat_entry.pack()

        tk.Label(win, text="Longitude").pack()
        lon_entry = tk.Entry(win)
        lon_entry.pack()

        def save_new_location():
            name = name_entry.get().strip()
            try:
                lat = float(lat_entry.get())
                lon = float(lon_entry.get())
            except ValueError:
                messagebox.showerror("Error", "Latitude and longitude must be numbers")
                return

            if not name:
                messagebox.showerror("Error", "Location name required")
                return

            self.locations[name] = {"lat": lat, "lon": lon}
            self.persist_state()

            self.location_combo["values"] = list(self.locations.keys())
            self.location_var.set(name)
            win.destroy()

        ttk.Button(win, text="Save", command=save_new_location).pack(pady=10)

    def open_config(self):
            win = tk.Toplevel(self)
            win.title("Configuration")
            win.geometry("360x180")
            win.resizable(False, False)

            # Snow
            snow_frame = tk.Frame(win)
            snow_frame.pack(fill="x", padx=10, pady=(12, 6))

            snow_cb = tk.Checkbutton(
                snow_frame,
                text="Highlight snow over:",
                variable=self.highlight_snow_enabled,
            )
            snow_cb.pack(side=tk.LEFT)

            snow_entry = ttk.Entry(snow_frame, width=8, textvariable=self.highlight_snow_over_cm)
            snow_entry.pack(side=tk.LEFT, padx=6)
            tk.Label(snow_frame, text="cm").pack(side=tk.LEFT)

            # Rain
            rain_frame = tk.Frame(win)
            rain_frame.pack(fill="x", padx=10, pady=6)

            rain_cb = tk.Checkbutton(
                rain_frame,
                text="Highlight rain over:",
                variable=self.highlight_rain_enabled,
            )
            rain_cb.pack(side=tk.LEFT)

            rain_entry = ttk.Entry(rain_frame, width=8, textvariable=self.highlight_rain_over_mm)
            rain_entry.pack(side=tk.LEFT, padx=6)
            tk.Label(rain_frame, text="mm").pack(side=tk.LEFT)

            # Buttons
            btns = tk.Frame(win)
            btns.pack(fill="x", padx=10, pady=(12, 10))

            def apply_and_close():
                # Validate numeric inputs (avoid Tk crashes later)
                try:
                    float(self.highlight_snow_over_cm.get())
                    float(self.highlight_rain_over_mm.get())
                except Exception:
                    messagebox.showerror("Error", "Threshold values must be numbers.")
                    return
                self.persist_state()
                win.destroy()

            ttk.Button(btns, text="OK", command=apply_and_close).pack(side=tk.RIGHT)
            ttk.Button(btns, text="Cancel", command=win.destroy).pack(side=tk.RIGHT, padx=8)


if __name__ == "__main__":
    app = WeatherApp()
    app.mainloop()
