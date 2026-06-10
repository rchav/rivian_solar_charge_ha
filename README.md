# Rivian Solar Charging — Home Assistant Integration

Automatically charges your Rivian EV using excess solar power, after your home battery reaches 100%. Dynamically adjusts the Rivian on-board charger amperage every 5 minutes (configurable) so that solar surplus goes into the car instead of back to the grid.
<img width="2149" height="1104" alt="image" src="https://github.com/user-attachments/assets/9800010e-3bac-4068-8869-c6165e27db45" />

---

## How it works

1. Reads your **home battery %** and **grid power** from existing HA entities.
2. Reads your **Rivian vehicle state** (plugged in? battery level? location?) via the Rivian GraphQL API.
3. Applies the charging algorithm (see below).
4. Updates the **Rivian Charging Schedule** via GraphQL — no EVSE hardware changes required.

### Algorithm

**Powerwall hysteresis** (prevents thrashing on passing clouds):
- Start diverting to car when battery reaches **100%** (configurable)
- Keep charging until battery drops below **70%** (configurable stop threshold)
- Between 70–100%, hold whatever charging state is already active

**Amp calculation** (same formula as [ostap-korkuna/rivian-charging-automation](https://github.com/ostap-korkuna/rivian-charging-automation)):
```
Δ amps = floor(export_watts / 240)
```
Dead-band of ±2A prevents constant small adjustments. Clamped to Rivian's on-board charger range: **8–48A** (or 0 = off).

**Ramp down** gracefully steps amps down 8A per cycle instead of cutting instantly.

**Sunset cutoff** stops charging 30 minutes before sunset using HA's built-in sun position.

**Away from home** skips schedule updates when the car's GPS puts it outside a 0.5km radius of home.

**Rivian top-of-charge protection** won't start a new session if the car is already above 80% (configurable) — avoids stressing the top of the battery range.

**Charge Now** override bypasses all solar logic and charges at 48A immediately — useful for charging from grid or battery when needed. Auto-cancels when the car reaches its charge limit.

---

## Requirements

- Home Assistant 2023.6+
- A home battery / solar system already integrated in HA with:
  - A **battery state of charge** sensor (0–100%)
  - A **grid power** sensor where **negative = exporting to grid**
- Rivian account with a vehicle in delivered status
- Your Rivian **Vehicle ID** — find it by:
  - Running `python3 get_vehicle_id.py` (see below), or
  - Checking **Settings → Devices & Services → [Your Rivian Integration] → Device → Identifiers**

### Finding your Vehicle ID

```bash
python3 - << 'SCRIPT'
import urllib.request, json, getpass

email = input("Rivian email: ")
password = getpass.getpass("Rivian password: ")

def gql(payload, headers={}):
    req = urllib.request.Request(
        "https://rivian.com/api/gql/gateway/graphql",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "apollographql-client-name": "com.rivian.android.consumer", **headers}
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

csrf = gql({"operationName":"CreateCSRFToken","variables":[],"query":"mutation CreateCSRFToken { createCsrfToken { csrfToken appSessionToken } }"})["data"]["createCsrfToken"]
hdrs = {"a-sess": csrf["appSessionToken"], "csrf-token": csrf["csrfToken"]}

login = gql({"operationName":"Login","variables":{"email":email,"password":password},"query":"mutation Login($email:String!,$password:String!){login(email:$email,password:$password){__typename...on MobileLoginResponse{userSessionToken}...on MobileMFALoginResponse{otpToken}}}"}, hdrs)["data"]["login"]

if login["__typename"] == "MobileMFALoginResponse":
    otp = input("OTP from your phone: ")
    login = gql({"operationName":"LoginWithOTP","variables":{"email":email,"otpCode":otp,"otpToken":login["otpToken"]},"query":"mutation LoginWithOTP($email:String!,$otpCode:String!,$otpToken:String!){loginWithOTP(email:$email,otpCode:$otpCode,otpToken:$otpToken){userSessionToken}}"}, hdrs)["data"]["loginWithOTP"]

hdrs["u-sess"] = login["userSessionToken"]
result = gql({"operationName":"CurrentUserForLogin","variables":{},"query":"query CurrentUserForLogin{currentUser{vehicles{id vin name}}}"}, hdrs)
for v in result["data"]["currentUser"]["vehicles"]:
    print(f"Name: {v['name']}  VIN: {v['vin']}  ID: {v['id']}")
SCRIPT
```

---

## Installation

### HACS (recommended)
1. HACS → Integrations → ⋮ → Custom repositories
2. Add: `https://github.com/rchav/rivian_solar_charge_ha` — Type: **Integration**
3. Install **Rivian Solar Charging**
4. Restart HA

### Manual
Copy `custom_components/rivian_solar_charging/` into your HA `config/custom_components/` folder and restart.

---

## Setup

1. **Settings → Devices & Services → Add Integration → Rivian Solar Charging**
2. Enter your Rivian email and password
3. Enter the OTP sent to your phone
4. Configure:

| Field | Description |
|---|---|
| Vehicle ID | From the script above (e.g. `01-xxxxxxxxx`) |
| Powerwall % entity | Your battery SOC sensor |
| Grid power entity | Grid sensor — **must be negative when exporting** |
| Home lat/lng | Auto-filled from your HA home zone |
| Poll interval | How often to adjust (default 300s = 5 min) |
| EV charge target % | Stop solar-charging above this (default 90%) |
| Powerwall start % | Begin diverting to car when PW reaches this (default 100%) |
| Powerwall stop % | Stop diverting when PW drops below this (default 70%) |
| Rivian session start limit | Don't start new session above this Rivian % (default 80%) |

### Grid power sensor sign convention

This integration expects **negative = exporting**. This matches Tesla Powerwall, Enphase, and most common integrations. If your sensor is positive-on-export, create a HA template sensor that negates it:

```yaml
template:
  - sensor:
      - name: "Grid Power (negated)"
        unit_of_measurement: "W"
        state: "{{ -states('sensor.your_grid_power') | float }}"
```

---

## Entities created

| Entity | Description |
|---|---|
| `switch.rivian_solar_charging` | Master on/off for the automation |
| `switch.rivian_charge_now` | Bypass solar logic — charge at 48A immediately |
| `sensor.solar_charging_state` | idle / active / rampdown / charge_now |
| `sensor.rivian_target_charge_amps` | Current target amperage |
| `sensor.solar_export_power` | Calculated solar surplus (W) |
| `sensor.rivian_battery_level` | Car battery % (from Rivian API) |
| `sensor.rivian_charger_state` | Rivian charger connection state |
| `sensor.rivian_plugged_in` | True/False |
| `sensor.rivian_at_home` | True/False (geo-fence) |
| `sensor.rivian_distance_from_home` | Distance in km |
| `sensor.powerwall_state_of_charge` | Battery % (from your HA entity) |
| `sensor.after_sunset_cutoff` | True when past sunset cutoff window |
| `sensor.solar_charging_skip_reason` | Why charging is paused |

---

## Options (Settings → gear icon — no restart needed)

- Poll interval
- EV charge target %
- Powerwall start/stop thresholds
- Rivian session start limit

---

## Notes

- This uses the **unofficial Rivian GraphQL API**. Rivian may change it at any time.
- The integration **owns the Rivian charging schedule** — it will overwrite whatever schedule is set in the Rivian app. Save your existing schedule if needed before installing.
- Set your EVSE to **always-on** / disable smart features on the charger hardware itself.
- Tokens are refreshed silently using the OAuth refresh token — you should not need to re-authenticate after initial setup unless you change your Rivian password.

---

## Credits

- API mechanics from [ostap-korkuna/rivian-charging-automation](https://github.com/ostap-korkuna/rivian-charging-automation)
- API documentation from [kaedenbrinkman/rivian-api](https://github.com/kaedenbrinkman/rivian-api)
