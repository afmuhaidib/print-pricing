# 3D Print Pricing Calculator

A local web app that calculates a sale price for 3D prints from an already-sliced
Bambu Studio `.gcode` file. Upload a gcode file, the app parses the print time and
filament weight from the embedded slicer comments, and computes a price using
configurable cost/profit settings.

## Run it

Requires Python 3.8+.

```bash
pip install -r requirements.txt
python app.py
```

The app binds to **http://localhost:2222**.

## How it works

1. Upload a `.gcode` file exported by Bambu Studio.
2. The server scans all `;` comment lines for filament weight (`total filament weight`)
   and print time (`estimated printing time` / `model printing time`) metadata.
3. If either value can't be found, you can enter it manually — the weight/time
   fields are always editable before calculating.
4. Click **Calculate price** to get a full cost breakdown and final sale price in SAR.

## Settings

Click the "Settings" section to edit and persist:

- **Material price** (SAR/kg)
- **Machine rate** (SAR/hour)
- **Waste/failure buffer** (%)
- **Profit margin** (%)

Settings are saved to `settings.json` in the project directory and persist between runs.

## Pricing formula

```
material_cost   = grams_used * (material_price_per_kg / 1000)
machine_cost    = print_hours * machine_rate_per_hour
subtotal        = material_cost + machine_cost
buffer_amount   = subtotal * (waste_failure_buffer_percent / 100)
cost_with_buffer = subtotal + buffer_amount
profit_amount   = cost_with_buffer * (profit_margin_percent / 100)
final_price     = cost_with_buffer + profit_amount
```

All currency values are rounded to 2 decimal places.
