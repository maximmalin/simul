import { useEffect, useState } from 'react';
import {
  Cloud, Sun, CloudRain, CloudSnow, CloudFog, Wind,
  Droplets, Gauge, Radiation, Eye, Thermometer, Loader2,
  MapPin, Sunrise, Sunset, type LucideIcon,
} from 'lucide-react';

interface WeatherData {
  city: string;
  region: string;
  country: string;
  latitude: number;
  longitude: number;
  timezone: string;
  temperature: number;
  feelsLike: number;
  humidity: number;
  pressure: number;
  windSpeed: number;
  windDirection: number;
  visibility: number;
  cloudCover: number;
  uvIndex: number;
  weatherCode: number;
  isDay: boolean;
  sunrise: string;
  sunset: string;
  radiation: number;
  radDescription: string;
}

function interpretWeatherCode(code: number): { label: string; icon: LucideIcon } {
  if (code === 0) return { label: 'Clear', icon: Sun };
  if (code <= 3) return { label: 'Overcast', icon: Cloud };
  if (code <= 48) return { label: 'Fog', icon: CloudFog };
  if (code <= 57) return { label: 'Drizzle', icon: CloudRain };
  if (code <= 67) return { label: 'Rain', icon: CloudRain };
  if (code <= 77) return { label: 'Snow', icon: CloudSnow };
  if (code <= 82) return { label: 'Showers', icon: CloudRain };
  if (code <= 86) return { label: 'Snow', icon: CloudSnow };
  if (code >= 95) return { label: 'Thunderstorm', icon: CloudRain };
  return { label: 'Unknown', icon: Cloud };
}

function calcRadiation(lat: number, uvIndex: number, isDay: boolean): { value: number; description: string } {
  if (!isDay) return { value: 0.08, description: 'Background' };
  const base = 0.12;
  const uvComponent = uvIndex * 0.15;
  const total = base + uvComponent;
  let desc = 'Normal';
  if (total > 1.5) desc = 'Elevated';
  if (total > 2.5) desc = 'High — Caution';
  if (total > 4.0) desc = 'Hazardous';
  return { value: Math.round(total * 100) / 100, description: desc };
}

function windDirName(deg: number): string {
  const dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  return dirs[Math.round(deg / 22.5) % 16];
}

export function WeatherBar() {
  const [weather, setWeather] = useState<WeatherData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetchWeatherByIP();
  }, []);

  const fetchWeatherByIP = async () => {
    try {
      const ipRes = await fetch('https://ipapi.co/json/');
      if (!ipRes.ok) throw new Error('IP lookup failed');
      const ipData = await ipRes.json();
      const { latitude, longitude, city, region, country_name, timezone } = ipData;

      if (!latitude || !longitude) throw new Error('No geolocation');

      const weatherRes = await fetch(
        `https://api.open-meteo.com/v1/forecast?latitude=${latitude}&longitude=${longitude}&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,cloud_cover,pressure_msl,wind_speed_10m,wind_direction_10m,visibility,uv_index&daily=sunrise,sunset&timezone=auto`
      );
      if (!weatherRes.ok) throw new Error('Weather fetch failed');
      const wData = await weatherRes.json();

      const current = wData.current;
      const daily = wData.daily;
      const uv = current.uv_index ?? 0;
      const isDay = current.is_day === 1;
      const rad = calcRadiation(latitude, uv, isDay);

      setWeather({
        city: city ?? 'Unknown',
        region: region ?? '',
        country: country_name ?? '',
        latitude,
        longitude,
        timezone: timezone ?? 'UTC',
        temperature: Math.round(current.temperature_2m),
        feelsLike: Math.round(current.apparent_temperature),
        humidity: current.relative_humidity_2m,
        pressure: Math.round(current.pressure_msl),
        windSpeed: Math.round(current.wind_speed_10m),
        windDirection: current.wind_direction_10m,
        visibility: Math.round(current.visibility / 1000),
        cloudCover: current.cloud_cover,
        uvIndex: uv,
        weatherCode: current.weather_code,
        isDay,
        sunrise: daily?.sunrise?.[0] ?? '',
        sunset: daily?.sunset?.[0] ?? '',
        radiation: rad.value,
        radDescription: rad.description,
      });
      setLoading(false);
    } catch {
      setError(true);
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="border-b border-zone-400/20 bg-zone-100/50 px-4 py-1.5 flex items-center gap-2">
        <Loader2 className="w-3 h-3 text-zone-600 animate-spin" />
        <span className="font-mono text-[9px] tracking-widest text-zone-600 uppercase">
          Acquiring environmental telemetry...
        </span>
      </div>
    );
  }

  if (error || !weather) {
    return (
      <div className="border-b border-zone-400/20 bg-zone-100/50 px-4 py-1.5 flex items-center gap-2">
        <span className="status-dot-offline" />
        <span className="font-mono text-[9px] tracking-widest text-zone-600 uppercase">
          Environmental telemetry unavailable
        </span>
      </div>
    );
  }

  const w = interpretWeatherCode(weather.weatherCode);
  const WeatherIcon = w.icon;
  const tempColor = weather.temperature > 25 ? 'text-amber-400' : weather.temperature < 5 ? 'text-steel-400' : 'text-toxic-400';
  const radColor = weather.radiation > 2.5 ? 'text-blood-500' : weather.radiation > 1.5 ? 'text-amber-400' : 'text-toxic-400';

  const sunriseTime = weather.sunrise ? new Date(weather.sunrise).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false }) : '--:--';
  const sunsetTime = weather.sunset ? new Date(weather.sunset).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false }) : '--:--';

  return (
    <div className="border-b border-zone-400/20 bg-zone-100/50 overflow-hidden">
      <div className="flex items-center gap-0 overflow-x-auto px-3 py-1.5">
        {/* Location */}
        <div className="flex items-center gap-1.5 px-2.5 flex-shrink-0 border-r border-zone-400/15">
          <MapPin className="w-3 h-3 text-blood-500" />
          <span className="font-mono text-[9px] tracking-wider text-zone-700 uppercase">
            {weather.city}
          </span>
          <span className="font-mono text-[8px] text-zone-600">
            {weather.country}
          </span>
        </div>

        {/* Weather condition */}
        <div className="flex items-center gap-1.5 px-2.5 flex-shrink-0 border-r border-zone-400/15">
          <WeatherIcon className={`w-3 h-3 ${weather.isDay ? 'text-amber-400' : 'text-steel-400'}`} />
          <span className="font-mono text-[9px] tracking-wider text-zone-700 uppercase">
            {w.label}
          </span>
        </div>

        {/* Temperature */}
        <div className="flex items-center gap-1.5 px-2.5 flex-shrink-0 border-r border-zone-400/15">
          <Thermometer className={`w-3 h-3 ${tempColor}`} />
          <span className={`font-mono text-[10px] font-bold ${tempColor}`}>
            {weather.temperature}°C
          </span>
          <span className="font-mono text-[8px] text-zone-600">
            feels {weather.feelsLike}°
          </span>
        </div>

        {/* Humidity */}
        <div className="flex items-center gap-1.5 px-2.5 flex-shrink-0 border-r border-zone-400/15">
          <Droplets className="w-3 h-3 text-steel-400" />
          <span className="font-mono text-[9px] text-zone-700">
            {weather.humidity}%
          </span>
        </div>

        {/* Pressure */}
        <div className="flex items-center gap-1.5 px-2.5 flex-shrink-0 border-r border-zone-400/15">
          <Gauge className="w-3 h-3 text-zone-600" />
          <span className="font-mono text-[9px] text-zone-700">
            {weather.pressure} hPa
          </span>
        </div>

        {/* Wind */}
        <div className="flex items-center gap-1.5 px-2.5 flex-shrink-0 border-r border-zone-400/15">
          <Wind className="w-3 h-3 text-toxic-400" />
          <span className="font-mono text-[9px] text-zone-700">
            {weather.windSpeed} km/h
          </span>
          <span className="font-mono text-[8px] text-zone-600">
            {windDirName(weather.windDirection)}
          </span>
        </div>

        {/* Visibility */}
        <div className="flex items-center gap-1.5 px-2.5 flex-shrink-0 border-r border-zone-400/15">
          <Eye className="w-3 h-3 text-zone-600" />
          <span className="font-mono text-[9px] text-zone-700">
            {weather.visibility} km
          </span>
        </div>

        {/* Cloud cover */}
        <div className="flex items-center gap-1.5 px-2.5 flex-shrink-0 border-r border-zone-400/15">
          <Cloud className="w-3 h-3 text-zone-600" />
          <span className="font-mono text-[9px] text-zone-700">
            {weather.cloudCover}%
          </span>
        </div>

        {/* UV Index */}
        <div className="flex items-center gap-1.5 px-2.5 flex-shrink-0 border-r border-zone-400/15">
          <Sun className={`w-3 h-3 ${weather.uvIndex > 6 ? 'text-amber-400' : 'text-toxic-400'}`} />
          <span className="font-mono text-[9px] text-zone-700">
            UV {weather.uvIndex.toFixed(1)}
          </span>
        </div>

        {/* Radiation */}
        <div className="flex items-center gap-1.5 px-2.5 flex-shrink-0 border-r border-zone-400/15">
          <Radiation className={`w-3 h-3 ${radColor} ${weather.radiation > 1.5 ? 'animate-geiger' : ''}`} />
          <span className={`font-mono text-[10px] font-bold ${radColor}`}>
            {weather.radiation} R/h
          </span>
          <span className="font-mono text-[8px] text-zone-600">
            {weather.radDescription}
          </span>
        </div>

        {/* Sunrise */}
        <div className="flex items-center gap-1.5 px-2.5 flex-shrink-0 border-r border-zone-400/15">
          <Sunrise className="w-3 h-3 text-amber-400" />
          <span className="font-mono text-[9px] text-zone-700">
            {sunriseTime}
          </span>
        </div>

        {/* Sunset */}
        <div className="flex items-center gap-1.5 px-2.5 flex-shrink-0 border-r border-zone-400/15">
          <Sunset className="w-3 h-3 text-rust-400" />
          <span className="font-mono text-[9px] text-zone-700">
            {sunsetTime}
          </span>
        </div>

        {/* Coordinates */}
        <div className="flex items-center gap-1.5 px-2.5 flex-shrink-0">
          <span className="font-mono text-[8px] text-zone-600">
            {weather.latitude.toFixed(2)}°, {weather.longitude.toFixed(2)}°
          </span>
        </div>
      </div>
    </div>
  );
}
