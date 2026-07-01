import json
import math
import os
import re

from flask import Flask, jsonify, request

app = Flask(__name__)

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

DEFAULT_SETTINGS = {
    "material_price_per_kg": 25,
    "machine_rate_per_hour": 0.50,
    "waste_failure_buffer_percent": 12,
    "profit_margin_percent": 25,
}


def load_settings():
    merged = dict(DEFAULT_SETTINGS)
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}
        for key in DEFAULT_SETTINGS:
            if key in data:
                try:
                    value = float(data[key])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value) and value >= 0:
                    merged[key] = value
    return merged


def save_settings(settings):
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)


if not os.path.exists(SETTINGS_PATH):
    save_settings(DEFAULT_SETTINGS)


def parse_time_to_hours(time_str):
    """Parse strings like '2h 35m', '1d 2h 3m', '45m', '3h' into hours."""
    time_str = time_str.strip()
    total_minutes = 0
    found = False

    day_match = re.search(r"(\d+)\s*d", time_str)
    hour_match = re.search(r"(\d+)\s*h", time_str)
    min_match = re.search(r"(\d+)\s*m(?!s)", time_str)

    if day_match:
        total_minutes += int(day_match.group(1)) * 24 * 60
        found = True
    if hour_match:
        total_minutes += int(hour_match.group(1)) * 60
        found = True
    if min_match:
        total_minutes += int(min_match.group(1))
        found = True

    if not found:
        num_match = re.search(r"([\d.]+)", time_str)
        if num_match:
            return round(float(num_match.group(1)), 4)
        return None

    return round(total_minutes / 60.0, 4)


def parse_gcode_metadata(text):
    """Search all comment lines for filament weight and print time metadata."""
    result = {"grams": None, "hours": None, "filament_name": None}

    comment_lines = [line.strip() for line in text.splitlines() if line.strip().startswith(";")]
    seen_weight_lines = set()

    total_weight = None
    weight_sum = 0.0
    weight_found = False

    hours_normal = None
    hours_silent = None
    hours_generic = None

    for line in comment_lines:
        lower = line.lower()

        if "total filament weight" in lower:
            m = re.search(r"total filament weight\s*\[?g?\]?\s*[:=]\s*([\d.]+)", lower)
            if m:
                total_weight = float(m.group(1))

        elif re.search(r"filament\s+(?:weight|used)\s*\[?g\]?\s*[:=]", lower):
            if lower not in seen_weight_lines:
                seen_weight_lines.add(lower)
                m = re.search(r"filament\s+(?:weight|used)\s*\[?g\]?\s*[:=]\s*([\d.,\s]+)", lower)
                if m:
                    nums = re.findall(r"[\d.]+", m.group(1))
                    for n in nums:
                        try:
                            weight_sum += float(n)
                            weight_found = True
                        except ValueError:
                            pass

        if "estimated printing time" in lower or "model printing time" in lower:
            m = re.search(r"(?:estimated printing time|model printing time).*?[:=]\s*(.+)", lower)
            if m:
                t = parse_time_to_hours(m.group(1))
                if t is not None:
                    if "silent mode" in lower:
                        hours_silent = hours_silent if hours_silent is not None else t
                    elif "normal mode" in lower:
                        hours_normal = hours_normal if hours_normal is not None else t
                    else:
                        hours_generic = hours_generic if hours_generic is not None else t

        if result["filament_name"] is None:
            m = re.search(r";\s*(?:filament_type|plate name)\s*[:=]\s*(.+)", line, re.IGNORECASE)
            if m:
                result["filament_name"] = m.group(1).strip()

    if hours_normal is not None:
        result["hours"] = hours_normal
    elif hours_generic is not None:
        result["hours"] = hours_generic
    elif hours_silent is not None:
        result["hours"] = hours_silent

    if total_weight is not None:
        result["grams"] = round(total_weight, 2)
    elif weight_found:
        result["grams"] = round(weight_sum, 2)

    return result


@app.route("/")
def index():
    return INDEX_HTML


@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(load_settings())


@app.route("/api/settings", methods=["POST"])
def update_settings():
    data = request.get_json(force=True)
    settings = load_settings()
    for key in DEFAULT_SETTINGS:
        if key in data:
            try:
                value = float(data[key])
            except (TypeError, ValueError):
                return jsonify({"error": f"Invalid value for {key}"}), 400
            if not math.isfinite(value) or value < 0:
                return jsonify({"error": f"{key} must be a non-negative number"}), 400
            settings[key] = value
    save_settings(settings)
    return jsonify(settings)


@app.route("/api/parse-gcode", methods=["POST"])
def parse_gcode():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    if f.filename == "":
        return jsonify({"error": "No file selected"}), 400

    try:
        raw = f.read()
        text = raw.decode("utf-8", errors="ignore")
    except Exception:
        return jsonify({"error": "Could not read file"}), 400

    parsed = parse_gcode_metadata(text)

    if parsed["grams"] is None and parsed["hours"] is None:
        parsed["error"] = (
            "Could not find filament weight or print time in this gcode file. "
            "Please enter values manually."
        )
    elif parsed["grams"] is None:
        parsed["error"] = "Could not find filament weight in this gcode file. Please enter it manually."
    elif parsed["hours"] is None:
        parsed["error"] = "Could not find print time in this gcode file. Please enter it manually."

    return jsonify(parsed)


@app.route("/api/calculate", methods=["POST"])
def calculate():
    data = request.get_json(force=True)
    settings = load_settings()

    try:
        grams = float(data["grams"])
        hours = float(data["hours"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "grams and hours must be numbers"}), 400

    material_price_per_kg = float(settings["material_price_per_kg"])
    machine_rate_per_hour = float(settings["machine_rate_per_hour"])
    waste_failure_buffer_percent = float(settings["waste_failure_buffer_percent"])
    profit_margin_percent = float(settings["profit_margin_percent"])

    material_cost = grams * (material_price_per_kg / 1000)
    machine_cost = hours * machine_rate_per_hour
    subtotal = material_cost + machine_cost
    buffer_amount = subtotal * (waste_failure_buffer_percent / 100)
    cost_with_buffer = subtotal + buffer_amount
    profit_amount = cost_with_buffer * (profit_margin_percent / 100)
    final_price = cost_with_buffer + profit_amount

    return jsonify({
        "grams": round(grams, 2),
        "hours": round(hours, 2),
        "material_cost": round(material_cost, 2),
        "machine_cost": round(machine_cost, 2),
        "buffer_amount": round(buffer_amount, 2),
        "profit_amount": round(profit_amount, 2),
        "final_price": round(final_price, 2),
    })


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>3D Print Pricing Calculator</title>
<style>
  :root {
    --bg: #0f172a;
    --card: #1e293b;
    --accent: #38bdf8;
    --accent2: #34d399;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --error: #f87171;
  }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    margin: 0;
    padding: 24px;
    display: flex;
    justify-content: center;
  }
  .container {
    width: 100%;
    max-width: 640px;
  }
  h1 {
    font-size: 1.5rem;
    margin-bottom: 4px;
  }
  .subtitle {
    color: var(--muted);
    margin-bottom: 24px;
    font-size: 0.9rem;
  }
  .card {
    background: var(--card);
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 20px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
  }
  .upload-area {
    border: 2px dashed #475569;
    border-radius: 10px;
    padding: 28px;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.2s;
  }
  .upload-area:hover, .upload-area.dragover {
    border-color: var(--accent);
  }
  .upload-area input { display: none; }
  .upload-label {
    color: var(--muted);
    font-size: 0.95rem;
  }
  .filename {
    margin-top: 10px;
    color: var(--accent);
    font-size: 0.9rem;
  }
  details.card {
    padding: 0;
  }
  details.card summary {
    padding: 16px 20px;
    cursor: pointer;
    font-weight: 600;
    list-style: none;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  details.card summary::-webkit-details-marker { display: none; }
  .settings-body {
    padding: 0 20px 20px 20px;
  }
  .field-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
    gap: 12px;
  }
  .field-row label {
    color: var(--muted);
    font-size: 0.9rem;
    flex: 1;
  }
  .field-row input {
    width: 120px;
    padding: 8px 10px;
    border-radius: 6px;
    border: 1px solid #475569;
    background: #0f172a;
    color: var(--text);
    font-size: 0.9rem;
  }
  button {
    background: var(--accent);
    color: #0f172a;
    border: none;
    padding: 10px 18px;
    border-radius: 8px;
    font-weight: 600;
    cursor: pointer;
    font-size: 0.9rem;
  }
  button:hover { opacity: 0.9; }
  button.secondary {
    background: transparent;
    border: 1px solid var(--accent);
    color: var(--accent);
  }
  .error-box {
    background: rgba(248, 113, 113, 0.1);
    border: 1px solid var(--error);
    color: var(--error);
    padding: 12px 16px;
    border-radius: 8px;
    margin-top: 16px;
    font-size: 0.9rem;
  }
  .manual-entry {
    margin-top: 16px;
    display: none;
  }
  .manual-entry.visible { display: block; }
  .breakdown {
    margin-top: 8px;
  }
  .breakdown-row {
    display: flex;
    justify-content: space-between;
    padding: 8px 0;
    border-bottom: 1px solid #334155;
    font-size: 0.9rem;
  }
  .breakdown-row:last-child { border-bottom: none; }
  .breakdown-row .label { color: var(--muted); }
  .final-price {
    margin-top: 16px;
    text-align: center;
    padding: 20px;
    background: linear-gradient(135deg, rgba(56,189,248,0.15), rgba(52,211,153,0.15));
    border-radius: 10px;
  }
  .final-price .amount {
    font-size: 2.4rem;
    font-weight: 700;
    color: var(--accent2);
  }
  .final-price .caption {
    color: var(--muted);
    font-size: 0.85rem;
    margin-top: 4px;
  }
  .calc-btn-row {
    margin-top: 16px;
    display: flex;
    gap: 10px;
  }
  .hidden { display: none; }
  .save-msg {
    color: var(--accent2);
    font-size: 0.85rem;
    margin-left: 10px;
  }
</style>
</head>
<body>
<div class="container">
  <h1>3D Print Pricing Calculator</h1>
  <div class="subtitle">Upload a Bambu Studio .gcode file to get a sale price quote (SAR)</div>

  <div class="card">
    <div class="upload-area" id="uploadArea">
      <input type="file" id="fileInput" accept=".gcode,.gco,.g">
      <div class="upload-label">Click to choose, or drag &amp; drop a .gcode file</div>
      <div class="filename" id="filename"></div>
    </div>
    <div id="parseError" class="error-box hidden"></div>

    <div class="manual-entry" id="manualEntry">
      <div class="field-row">
        <label for="manualGrams">Filament weight (g)</label>
        <input type="number" id="manualGrams" step="0.01" min="0">
      </div>
      <div class="field-row">
        <label for="manualHours">Print time (hours)</label>
        <input type="number" id="manualHours" step="0.01" min="0">
      </div>
      <div class="calc-btn-row">
        <button id="calcBtn">Calculate price</button>
      </div>
    </div>
  </div>

  <details class="card" id="settingsCard">
    <summary>Settings <span style="color: var(--muted); font-weight: 400; font-size: 0.85rem;">(click to expand)</span></summary>
    <div class="settings-body">
      <div class="field-row">
        <label for="materialPrice">Material price (SAR / kg)</label>
        <input type="number" id="materialPrice" step="0.01" min="0">
      </div>
      <div class="field-row">
        <label for="machineRate">Machine rate (SAR / hour)</label>
        <input type="number" id="machineRate" step="0.01" min="0">
      </div>
      <div class="field-row">
        <label for="bufferPercent">Waste/failure buffer (%)</label>
        <input type="number" id="bufferPercent" step="0.1" min="0">
      </div>
      <div class="field-row">
        <label for="profitPercent">Profit margin (%)</label>
        <input type="number" id="profitPercent" step="0.1" min="0">
      </div>
      <button id="saveSettingsBtn" class="secondary">Save settings</button>
      <span class="save-msg hidden" id="saveMsg">Saved!</span>
    </div>
  </details>

  <div class="card hidden" id="resultCard">
    <div class="breakdown">
      <div class="breakdown-row"><span class="label">Filament weight</span><span id="rGrams"></span></div>
      <div class="breakdown-row"><span class="label">Print time</span><span id="rHours"></span></div>
      <div class="breakdown-row"><span class="label">Material cost</span><span id="rMaterial"></span></div>
      <div class="breakdown-row"><span class="label">Machine cost</span><span id="rMachine"></span></div>
      <div class="breakdown-row"><span class="label">Waste/failure buffer</span><span id="rBuffer"></span></div>
      <div class="breakdown-row"><span class="label">Profit</span><span id="rProfit"></span></div>
    </div>
    <div class="final-price">
      <div class="amount" id="rFinal"></div>
      <div class="caption">Final sale price (SAR)</div>
    </div>
  </div>
</div>

<script>
const fileInput = document.getElementById('fileInput');
const uploadArea = document.getElementById('uploadArea');
const filenameEl = document.getElementById('filename');
const parseError = document.getElementById('parseError');
const manualEntry = document.getElementById('manualEntry');
const manualGrams = document.getElementById('manualGrams');
const manualHours = document.getElementById('manualHours');
const calcBtn = document.getElementById('calcBtn');
const resultCard = document.getElementById('resultCard');

const materialPrice = document.getElementById('materialPrice');
const machineRate = document.getElementById('machineRate');
const bufferPercent = document.getElementById('bufferPercent');
const profitPercent = document.getElementById('profitPercent');
const saveSettingsBtn = document.getElementById('saveSettingsBtn');
const saveMsg = document.getElementById('saveMsg');

function fmt(n) {
  return Number(n).toFixed(2) + ' SAR';
}

async function loadSettings() {
  try {
    const res = await fetch('/api/settings');
    const data = await res.json();
    materialPrice.value = data.material_price_per_kg;
    machineRate.value = data.machine_rate_per_hour;
    bufferPercent.value = data.waste_failure_buffer_percent;
    profitPercent.value = data.profit_margin_percent;
  } catch (err) {
    parseError.textContent = 'Could not load settings from the server.';
    parseError.classList.remove('hidden');
  }
}
loadSettings();

saveSettingsBtn.addEventListener('click', async () => {
  const payload = {
    material_price_per_kg: materialPrice.value,
    machine_rate_per_hour: machineRate.value,
    waste_failure_buffer_percent: bufferPercent.value,
    profit_margin_percent: profitPercent.value,
  };
  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (res.ok) {
      saveMsg.classList.remove('hidden');
      setTimeout(() => saveMsg.classList.add('hidden'), 2000);
    } else {
      parseError.textContent = data.error || 'Could not save settings.';
      parseError.classList.remove('hidden');
    }
  } catch (err) {
    parseError.textContent = 'Could not save settings — the server returned an unexpected response.';
    parseError.classList.remove('hidden');
  }
});

uploadArea.addEventListener('click', () => fileInput.click());
uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('dragover'); });
uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
uploadArea.addEventListener('drop', (e) => {
  e.preventDefault();
  uploadArea.classList.remove('dragover');
  if (e.dataTransfer.files.length) {
    fileInput.files = e.dataTransfer.files;
    handleFile(e.dataTransfer.files[0]);
  }
});
fileInput.addEventListener('change', () => {
  if (fileInput.files.length) handleFile(fileInput.files[0]);
});

async function handleFile(file) {
  filenameEl.textContent = file.name;
  parseError.classList.add('hidden');
  resultCard.classList.add('hidden');
  manualEntry.classList.remove('visible');

  const formData = new FormData();
  formData.append('file', file);

  let data;
  try {
    const res = await fetch('/api/parse-gcode', { method: 'POST', body: formData });
    data = await res.json();
  } catch (err) {
    parseError.textContent = 'Could not reach the server to parse this file. Please enter values manually.';
    parseError.classList.remove('hidden');
    manualGrams.value = '';
    manualHours.value = '';
    manualEntry.classList.add('visible');
    return;
  }

  if (data.error) {
    parseError.textContent = data.error;
    parseError.classList.remove('hidden');
  }

  manualGrams.value = data.grams !== null && data.grams !== undefined ? data.grams : '';
  manualHours.value = data.hours !== null && data.hours !== undefined ? data.hours : '';
  manualEntry.classList.add('visible');
}

calcBtn.addEventListener('click', async () => {
  const grams = parseFloat(manualGrams.value);
  const hours = parseFloat(manualHours.value);

  if (isNaN(grams) || isNaN(hours) || grams < 0 || hours < 0) {
    parseError.textContent = 'Please enter valid, non-negative numbers for weight and time.';
    parseError.classList.remove('hidden');
    return;
  }
  parseError.classList.add('hidden');

  let data;
  try {
    const res = await fetch('/api/calculate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ grams, hours }),
    });
    data = await res.json();
  } catch (err) {
    parseError.textContent = 'Could not reach the server to calculate a price. Please try again.';
    parseError.classList.remove('hidden');
    return;
  }

  if (data.error) {
    parseError.textContent = data.error;
    parseError.classList.remove('hidden');
    return;
  }

  document.getElementById('rGrams').textContent = data.grams + ' g';
  document.getElementById('rHours').textContent = data.hours + ' hrs';
  document.getElementById('rMaterial').textContent = fmt(data.material_cost);
  document.getElementById('rMachine').textContent = fmt(data.machine_cost);
  document.getElementById('rBuffer').textContent = fmt(data.buffer_amount);
  document.getElementById('rProfit').textContent = fmt(data.profit_amount);
  document.getElementById('rFinal').textContent = fmt(data.final_price);
  resultCard.classList.remove('hidden');
  resultCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=2222, debug=False)
