from flask import Flask, render_template_string, request, jsonify
from datetime import datetime, timedelta
import calendar
from pymongo import MongoClient
from dotenv import load_dotenv
import os
from functools import wraps

# Load environment variables from .env file (for local testing)
load_dotenv() 

app = Flask(__name__)

# --- 1. CONFIGURATION (LOAD FROM ENVIRONMENT) ---
# NOTE: Replace 'mongodb+srv://...' with your actual MongoDB Atlas connection string
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://user:pass@cluster.mongodb.net/financial_db?retryWrites=true&w=majority")
# This key is used to authorize API calls for adding/editing/deleting data
SECRET_KEY = os.getenv("SECRET_KEY", "A_FALLBACK_SECRET_KEY_NEVER_USE_IN_PROD") 
COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME", "user_data")

# Pass the secret key to the template for client-side authentication
app.jinja_env.globals['SECRET_KEY'] = SECRET_KEY 


# --- 2. DATABASE CONNECTION & INITIALIZATION ---

def get_mongo_collection():
    """Connects to MongoDB and returns the collection object."""
    try:
        # Connect to the client
        client = MongoClient(MONGO_URI)
        # Use the database name specified in the connection string
        db = client.get_database() 
        return db[COLLECTION_NAME]
    except Exception as e:
        print(f"FATAL: Could not connect to MongoDB: {e}")
        return None

def get_default_data():
    """Returns the initial/default data structure, including metadata."""
    today = datetime.now().strftime('%Y-%m-%d')
    return {
        'income': [
            # inc1: Salary represents the RECURRING amount (inflow rate)
            {'id': 'inc1', 'name': 'Monthly Salary', 'amount': 116938},
            # inc2: Current Balance represents the DYNAMIC account balance
            {'id': 'inc2', 'name': 'Current Account Balance', 'amount': 45000},
        ],
        'expenses': [
            {'id': 'exp1', 'name': 'Rent', 'amount': 11000},
            {'id': 'exp2', 'name': 'Cook', 'amount': 1500},
            {'id': 'exp3', 'name': 'Groceries', 'amount': 3000},
            {'id': 'exp4', 'name': 'Travelling', 'amount': 500},
            {'id': 'exp5', 'name': 'Pakhi-Travelling', 'amount': 4000},
            {'id': 'exp6', 'name': 'Pakhi', 'amount': 5000},
        ],
        'investments': [
            {'id': 'inv1', 'name': 'Mutual Funds', 'amount': 17500},
            {'id': 'inv2', 'name': 'Stocks', 'amount': 17500},
            {'id': 'inv3', 'name': 'Liquid Fund', 'amount': 35000},
        ],
        'debts': [
            {'id': 'dbt1', 'name': 'Divyam', 'amount': 6500, 'monthlyPayment': 2000},
            {'id': 'dbt2', 'name': 'Dada', 'amount': 50000, 'monthlyPayment': 5000},
        ],
        'purchases': [ 
            {'id': 'pur1', 'name': 'Furniture (Sofa)', 'amount': 35000, 'date': today},
        ],
        # Metadata to prevent double-counting of automatic monthly actions
        'metadata': {
            'last_balance_update_month': None, 
            'last_debt_payment_month': None    
        }
    }

def save_data(data_to_save):
    """Saves the current data dictionary to the MongoDB document."""
    collection = get_mongo_collection()
    if collection is None:
        print("Error: Database connection failed. Cannot save data.")
        return
        
    # Replace the existing document. Assumes only one document per user/app.
    # upsert=True ensures a document is created if it doesn't exist (though load_data handles initial creation)
    collection.replace_one({}, data_to_save, upsert=True) 

# --- 3. AUTHENTICATION DECORATOR ---

def requires_auth(f):
    """Decorator to protect API endpoints using a secret key in the request header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Check for the secret key in the custom header
        auth_key = request.headers.get('X-Auth-Key')
        if auth_key and auth_key == SECRET_KEY:
            return f(*args, **kwargs)
        
        print(f"Authentication failed: Key received '{auth_key}' vs Key expected '{SECRET_KEY}'")
        return jsonify({"message": "Authentication Required: Invalid 'X-Auth-Key' header."}), 401
    return decorated


# --- 4. UTILITY FUNCTIONS (DATE LOGIC) ---

def is_working_day(date_obj):
    """Checks if a date is Monday (0) through Friday (4)."""
    return date_obj.weekday() < 5

def get_last_working_day(year, month, offset=1):
    """Calculates the Nth last working day (Mon-Fri) of a month."""
    
    last_day_num = calendar.monthrange(year, month)[1]
    day_to_check = datetime(year, month, last_day_num).date()
    
    working_days_count = 0
    
    while working_days_count < offset and day_to_check.month == month:
        if is_working_day(day_to_check):
            working_days_count += 1
            if working_days_count == offset:
                return day_to_check

        day_to_check -= timedelta(days=1)
        
    return None 

def get_salary_date(year, month):
    """Salary is the 3rd last working day of the month (offset=3)."""
    return get_last_working_day(year, month, offset=3)

# --- 5. DYNAMIC UPDATE LOGIC ---

def check_and_update_balance():
    """
    Performs time-based updates: salary credit and debt reduction.
    Saves data if modified.
    """
    global data
    
    current_date = datetime.now().date()
    current_month_str = current_date.strftime('%Y-%m')
    current_year = current_date.year
    current_month = current_date.month

    # Ensure metadata structure exists
    metadata = data.setdefault('metadata', {'last_balance_update_month': None, 'last_debt_payment_month': None})
    
    data_modified = False
    
    # Locate the critical items
    salary_item = next((item for item in data.get('income', []) if item.get('id') == 'inc1'), None)
    balance_item = next((item for item in data.get('income', []) if item.get('id') == 'inc2'), None)

    # 1. Automatic Debt Reduction (Executed on the last calendar day of the month)
    
    # Calculate the last calendar day of the current month
    next_month = current_date.replace(day=28) + timedelta(days=4)
    last_day_of_month = next_month.replace(day=1) - timedelta(days=1)
    
    is_eom = current_date == last_day_of_month
    
    if is_eom and metadata['last_debt_payment_month'] != current_month_str:
        print(f"Executing monthly debt payment for {current_month_str}...")
        
        total_payment = sum(debt.get('monthlyPayment', 0) for debt in data.get('debts', []))

        if balance_item:
            # 1a. Reduce Current Balance by total debt payment
            balance_item['amount'] = balance_item.get('amount', 0) - total_payment
        
            # 1b. Reduce outstanding debt amount
            for debt in data.get('debts', []):
                try:
                    amount = debt.get('amount', 0)
                    payment = debt.get('monthlyPayment', 0)
                    debt['amount'] = max(0, amount - payment)
                except (KeyError, TypeError):
                    continue
        
            metadata['last_debt_payment_month'] = current_month_str
            data_modified = True
            print("Debt reduction completed.")

    # 2. Salary Credit Logic (Executed on the 3rd Last Working Day)
    try:
        salary_date = get_salary_date(current_year, current_month)
    except Exception:
        salary_date = None
        
    if salary_date and current_date >= salary_date and metadata['last_balance_update_month'] != current_month_str:
        print(f"Executing salary credit for {current_month_str} on {salary_date}...")
        
        if salary_item and balance_item:
            salary_amount = salary_item.get('amount', 0)
            balance_item['amount'] = balance_item.get('amount', 0) + salary_amount
            metadata['last_balance_update_month'] = current_month_str
            data_modified = True
            print("Salary credited.")

    if data_modified:
        save_data(data)
        return True
    
    return False

def load_data():
    """Fetches data from MongoDB or creates a new document."""
    global data
    collection = get_mongo_collection()
    
    if collection is None:
        # Fallback to in-memory default if DB fails
        data = get_default_data()
        return data

    # Find the single document storing all the user's data
    data = collection.find_one({})
    
    if data is None:
        # Create the initial document if none exists
        data = get_default_data()
        # Insert the initial data
        collection.insert_one(data)
    else:
        # Remove the MongoDB internal ID before use
        data.pop('_id', None) 
        
    # Check and perform automatic updates (debt payment, salary credit)
    check_and_update_balance() 
    
    return data

# Load the data when the application starts
data = load_data()

# --- 6. CALCULATION HELPER ---

def calculate_totals():
    global data 
    
    # --- Current Month Filtering ---
    today = datetime.now()
    current_month_year = (today.year, today.month)
    
    current_month_purchases_total = 0
    total_all_time_purchases = 0
    
    for item in data.get('purchases', []):
        try:
            amount = float(item.get('amount', 0))
            total_all_time_purchases += amount
            purchase_date = datetime.strptime(item.get('date', '1900-01-01'), '%Y-%m-%d')
            
            if (purchase_date.year, purchase_date.month) == current_month_year:
                current_month_purchases_total += amount
        except (ValueError, TypeError):
            continue

    # --- Income/Balance Calculation ---
    balance_item = next((item for item in data.get('income', []) if item.get('id') == 'inc2'), {'amount': 0})
    current_balance = balance_item.get('amount', 0)
    
    # totalIncomeRate: Sum of all income items EXCEPT the 'Current Account Balance' item (inc2)
    total_income_rate = sum(item['amount'] for item in data.get('income', []) if item.get('id') != 'inc2')

    # --- Outflow Calculation ---
    total_expenses = sum(item['amount'] for item in data.get('expenses', []))
    total_investments = sum(item['amount'] for item in data.get('investments', []))
    total_debt_payment = sum(item.get('monthlyPayment', 0) for item in data.get('debts', []))
    total_debt = sum(item['amount'] for item in data.get('debts', []))
    
    # Monthly Recurring Outflow for *Budgeting Forecast*
    monthly_recurring_outflow = total_expenses + total_investments + total_debt_payment
    
    # Total Outflow (used for the Total Cash Flow Movement chart/stat)
    total_outflow_for_stats = monthly_recurring_outflow + current_month_purchases_total
    
    # Monthly Forecast Remaining calculation
    remaining_after_recurring = total_income_rate - monthly_recurring_outflow

    return {
        'currentBalance': current_balance, # The actual cash in hand
        'totalIncomeRate': total_income_rate, # The monthly income rate (e.g., salary only)
        
        'totalExpenses': total_expenses,
        'totalInvestments': total_investments,
        'totalDebt': total_debt,
        'totalDebtPayment': total_debt_payment,
        
        'currentMonthPurchasesTotal': current_month_purchases_total,
        'totalAllTimePurchases': total_all_time_purchases,
        
        'totalOutflow': total_outflow_for_stats, # Total money movement this month
        'remainingBalance': remaining_after_recurring, # Forecasted remaining money from recurring flow
        'isPositive': remaining_after_recurring >= 0
    }


# --- 7. FLASK ROUTES / API ENDPOINTS ---

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/data')
def get_data():
    totals = calculate_totals()
    return jsonify({
        **data,
        **totals
    })

@app.route('/api/add', methods=['POST'])
@requires_auth # Protected
def add_item():
    global data
    req = request.json
    category = req.get('category')
    item = req.get('item')
    
    if category in data and isinstance(data[category], list):
        data[category].append(item)
        save_data(data)
    
    totals = calculate_totals()
    return jsonify({'success': True, **totals})

@app.route('/api/update', methods=['POST'])
@requires_auth # Protected
def update_item():
    global data
    req = request.json
    category = req.get('category')
    item_id = req.get('id')
    field = req.get('field')
    value = req.get('value')
    
    data_modified = False
    
    if category in data and isinstance(data[category], list):
        for item in data[category]:
            if item.get('id') == item_id:
                # Handle numeric fields
                if field in ['amount', 'monthlyPayment']:
                    try:
                        item[field] = float(value)
                        data_modified = True
                    except ValueError:
                        print(f"Warning: Invalid number value '{value}' for field '{field}'")
                # Handle all other fields (name, date)
                else:
                    item[field] = value
                    data_modified = True
                break
        
        if data_modified:
            save_data(data)
    
    totals = calculate_totals()
    return jsonify({'success': True, **totals})

@app.route('/api/delete', methods=['POST'])
@requires_auth # Protected
def delete_item():
    global data
    req = request.json
    category = req.get('category')
    item_id = req.get('id')
    
    if category in data and isinstance(data[category], list):
        data[category] = [item for item in data[category] if item.get('id') != item_id]
        save_data(data)
    
    totals = calculate_totals()
    return jsonify({'success': True, **totals})


# --- 8. HTML TEMPLATE DEFINITION (Frontend Logic Update for Auth) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Financial Dashboard</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/3.9.1/chart.min.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        
        header {
            background: white;
            padding: 40px;
            border-radius: 16px;
            margin-bottom: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        }
        
        header h1 {
            font-size: 2.5em;
            color: #667eea;
            margin-bottom: 10px;
        }
        
        header p {
            color: #999;
            font-size: 1.1em;
        }
        
        .cards-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .card {
            background: white;
            padding: 25px;
            border-radius: 12px;
            box-shadow: 0 5px 20px rgba(0,0,0,0.08);
            border-left: 5px solid;
        }
        
        .card.positive {
            border-left-color: #11998e;
        }
        
        .card.negative {
            border-left-color: #eb3349;
        }
        
        .card.neutral {
            border-left-color: #667eea;
        }
        
        .card.warning {
            border-left-color: #f5a622;
        }
        
        .card-label {
            font-size: 0.9em;
            color: #999;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .card-value {
            font-size: 2em;
            font-weight: bold;
            margin-bottom: 5px;
        }
        
        .card.positive .card-value {
            color: #11998e;
        }
        
        .card.negative .card-value {
            color: #eb3349;
        }
        
        .card-status {
            font-size: 0.85em;
            color: #999;
        }
        
        .tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        
        .tab-btn {
            background: white;
            border: none;
            padding: 12px 24px;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 500;
            transition: all 0.3s;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        }
        
        .tab-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(0,0,0,0.1);
        }
        
        .tab-btn.active {
            background: #667eea;
            color: white;
        }
        
        .section {
            background: white;
            border-radius: 12px;
            padding: 30px;
            box-shadow: 0 5px 20px rgba(0,0,0,0.08);
            display: none;
        }
        
        .section.active {
            display: block;
        }
        
        .section h2 {
            color: #667eea;
            margin-bottom: 20px;
            font-size: 1.8em;
        }
        
        .item-row {
            display: grid;
            grid-template-columns: 2fr 1fr 50px;
            gap: 15px;
            padding: 15px;
            border-bottom: 1px solid #f0f0f0;
            align-items: center;
            transition: all 0.2s;
        }
        
        .item-row:hover {
            background: #f9f9f9;
        }
        
        .item-row:last-child {
            border-bottom: none;
        }
        
        .item-row input {
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 1em;
            transition: all 0.2s;
        }
        
        .item-row input:focus {
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        
        .btn-delete {
            background: #ff4757;
            color: white;
            border: none;
            width: 40px;
            height: 40px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 1.2em;
            transition: all 0.2s;
        }
        
        .btn-delete:hover {
            background: #ff3838;
            transform: scale(1.1);
        }
        
        .btn-add {
            background: #667eea;
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 500;
            font-size: 1em;
            width: 100%;
            margin-top: 20px;
            transition: all 0.2s;
        }
        
        .btn-add:hover {
            background: #5568d3;
            transform: translateY(-2px);
        }
        
        .total-row {
            padding: 15px;
            background: #f9f9f9;
            border-radius: 8px;
            font-weight: bold;
            font-size: 1.1em;
            margin-top: 20px;
            display: flex;
            justify-content: space-between;
        }
        
        .debt-item {
            background: #f9f9f9;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 15px;
            border-left: 4px solid #f5a622;
        }
        
        .debt-item input {
            width: 100%;
            padding: 10px;
            margin: 10px 0;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 1em;
        }
        
        .debt-info {
            color: #11998e;
            font-size: 0.9em;
            margin-top: 10px;
            padding: 10px;
            background: #f0fffe;
            border-radius: 6px;
        }
        
        .chart-container {
            position: relative;
            height: 300px;
            margin-bottom: 30px;
        }
        
        footer {
            text-align: center;
            color: rgba(255,255,255,0.7);
            margin-top: 40px;
            padding: 20px;
        }
        
        .purchase-row {
            display: grid;
            grid-template-columns: 2fr 1fr 1fr 50px;
            gap: 15px;
            padding: 15px;
            border-bottom: 1px solid #f0f0f0;
            align-items: center;
            transition: all 0.2s;
        }
        .purchase-row:hover {
            background: #f9f9f9;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>💰 Financial Dashboard</h1>
            <p>Manage your monthly cash flow and track debt repayment (Data is persistent via MongoDB)</p>
        </header>
        
        <div class="cards-grid" id="statsCards"></div>
        
        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab('overview')">📊 Overview</button>
            <button class="tab-btn" onclick="switchTab('income')">💵 Income</button>
            <button class="tab-btn" onclick="switchTab('expenses')">💸 Expenses</button>
            <button class="tab-btn" onclick="switchTab('investments')">📈 Investments</button>
            <button class="tab-btn" onclick="switchTab('debts')">💳 Debts</button>
            <button class="tab-btn" onclick="switchTab('purchases')">🛒 Purchases</button> 
        </div>
        
        <div id="overview" class="section active">
            <h2>📊 Overview</h2>
            <div class="chart-container">
                <canvas id="cashFlowChart"></canvas>
            </div>
            <div class="chart-container">
                <canvas id="breakdownChart"></canvas>
            </div>
        </div>
        
        <div id="income" class="section">
            <h2>💵 Income Sources</h2>
            <div id="incomeList"></div>
            <button class="btn-add" onclick="addItem('income')">➕ Add Income Source</button>
        </div>
        
        <div id="expenses" class="section">
            <h2>💸 Monthly Expenses (Recurring)</h2>
            <div id="expensesList"></div>
            <button class="btn-add" onclick="addItem('expenses')">➕ Add Expense</button>
        </div>
        
        <div id="investments" class="section">
            <h2>📈 Investments (Monthly Contributions)</h2>
            <div id="investmentsList"></div>
            <button class="btn-add" onclick="addItem('investments')">➕ Add Investment</button>
        </div>
        
        <div id="debts" class="section">
            <h2>💳 Debt Repayment Planner</h2>
            <div id="debtsList"></div>
            <button class="btn-add" onclick="addItem('debts')">➕ Add Debt Record</button>
        </div>

        <div id="purchases" class="section"> 
            <h2>🛒 One-Time Purchases / Wants (Tracked by Date)</h2>
            <div id="purchasesList"></div>
            <button class="btn-add" onclick="addItem('purchases')">➕ Record New Purchase</button>
        </div>
        
        <footer>
            <p>💡 Key: Your current authentication key is <code>{{ SECRET_KEY }}</code>. This must be present in the X-Auth-Key header for modifications.</p>
        </footer>
    </div>
    
    <script>
        // CRITICAL: The server passes the SECRET_KEY to this variable for client-side authentication
        const AUTH_KEY = "{{ SECRET_KEY }}"; 

        let appData = {};
        let chartInstances = {};
        
        /**
         * Generic fetch function with built-in authentication header and error handling.
         */
        async function fetchData(url, method = 'GET', body = null) {
            const headers = { 'Content-Type': 'application/json' };
            
            // Add auth header only for modification requests
            if (method !== 'GET') {
                headers['X-Auth-Key'] = AUTH_KEY;
            }

            const options = { method, headers };
            if (body) {
                options.body = JSON.stringify(body);
            }

            try {
                const response = await fetch(url, options);

                if (response.status === 401) {
                     // Using console.error instead of alert as alert is blocked in many environments
                     console.error('Authentication failed. Check your AUTH_KEY and deployment settings.');
                     // Return empty data to prevent further processing
                     return null;
                }
                if (!response.ok) {
                    const errorBody = await response.json().catch(() => ({}));
                    console.error(`HTTP error! status: ${response.status}`, errorBody);
                    throw new Error('Failed to perform operation.');
                }
                return response.json();
            } catch (error) {
                console.error("Fetch error:", error);
                return null;
            }
        }

        async function loadData() {
            const result = await fetchData('/api/data', 'GET');
            if (result) {
                appData = result;
                render();
            }
        }
        
        function render() {
            renderStats();
            renderIncome();
            renderExpenses();
            renderInvestments();
            renderDebts();
            renderPurchases(); 
            renderCharts();
        }
        
        function renderStats() {
            const { currentBalance, totalIncomeRate, totalDebt, remainingBalance, isPositive, totalOutflow, currentMonthPurchasesTotal } = appData;
            
            const formatCurrency = (amount) => (amount || 0).toLocaleString('en-IN', {maximumFractionDigits: 0});

            const statsHTML = `
                <div class="card neutral">
                    <div class="card-label">Current Account Balance</div>
                    <div class="card-value">₹${formatCurrency(currentBalance)}</div>
                    <div class="card-status">Cash available for spending</div>
                </div>
                <div class="card ${isPositive ? 'positive' : 'negative'}">
                    <div class="card-label">Monthly Forecast Remaining</div>
                    <div class="card-value">₹${formatCurrency(remainingBalance)}</div>
                    <div class="card-status">Income Rate (${formatCurrency(totalIncomeRate)}) - Recurring Outflow</div>
                </div>
                <div class="card warning">
                    <div class="card-label">Total Outflow (This Month)</div>
                    <div class="card-value">₹${formatCurrency(totalOutflow)}</div>
                    <div class="card-status">Total Money Spent Incl. Purchases (₹${formatCurrency(currentMonthPurchasesTotal)})</div>
                </div>
                <div class="card negative">
                    <div class="card-label">Total Debt Outstanding</div>
                    <div class="card-value">₹${formatCurrency(totalDebt)}</div>
                    <div class="card-status">Principal amount before next payment</div>
                </div>
            `;
            
            document.getElementById('statsCards').innerHTML = statsHTML;
        }
        
        function renderIncome() {
            const formatCurrency = (amount) => (amount || 0).toLocaleString('en-IN', {maximumFractionDigits: 0});
            
            const html = appData.income.map(item => `
                <div class="item-row" style="${item.id === 'inc2' ? 'background: #f0f0ff;' : ''}">
                    <input type="text" value="${item.name}" onchange="updateField('income', '${item.id}', 'name', this.value)" ${item.id === 'inc2' ? 'readonly' : ''}>
                    <input type="number" value="${item.amount}" onchange="updateField('income', '${item.id}', 'amount', parseFloat(this.value))">
                    ${item.id === 'inc2' ? '<button class="btn-delete" disabled>🔒</button>' : `<button class="btn-delete" onclick="deleteItem('income', '${item.id}')">🗑️</button>`}
                </div>
            `).join('');
            
            document.getElementById('incomeList').innerHTML = html + `<div class="total-row">Total Income Rate <span>₹${formatCurrency(appData.totalIncomeRate)}</span></div>`;
        }
        
        function renderExpenses() {
            const formatCurrency = (amount) => (amount || 0).toLocaleString('en-IN', {maximumFractionDigits: 0});
            const html = appData.expenses.map(item => `
                <div class="item-row">
                    <input type="text" value="${item.name}" onchange="updateField('expenses', '${item.id}', 'name', this.value)">
                    <input type="number" value="${item.amount}" onchange="updateField('expenses', '${item.id}', 'amount', parseFloat(this.value))">
                    <button class="btn-delete" onclick="deleteItem('expenses', '${item.id}')">🗑️</button>
                </div>
            `).join('');
            
            document.getElementById('expensesList').innerHTML = html + `<div class="total-row">Total Expenses <span>₹${formatCurrency(appData.totalExpenses)}</span></div>`;
        }
        
        function renderInvestments() {
            const formatCurrency = (amount) => (amount || 0).toLocaleString('en-IN', {maximumFractionDigits: 0});
            const html = appData.investments.map(item => `
                <div class="item-row">
                    <input type="text" value="${item.name}" onchange="updateField('investments', '${item.id}', 'name', this.value)">
                    <input type="number" value="${item.amount}" onchange="updateField('investments', '${item.id}', 'amount', parseFloat(this.value))">
                    <button class="btn-delete" onclick="deleteItem('investments', '${item.id}')">🗑️</button>
                </div>
            `).join('');
            
            document.getElementById('investmentsList').innerHTML = html + `<div class="total-row">Total Investments <span>₹${formatCurrency(appData.totalInvestments)}</span></div>`;
        }
        
        function renderDebts() {
            const html = appData.debts.map(item => {
                const amount = parseFloat(item.amount) || 0;
                const monthlyPayment = parseFloat(item.monthlyPayment) || 0;

                const months = monthlyPayment > 0 ? (amount / monthlyPayment).toFixed(1) : '∞';
                
                return `
                    <div class="debt-item">
                        <input type="text" placeholder="Creditor Name" value="${item.name}" onchange="updateField('debts', '${item.id}', 'name', this.value)">
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                            <input type="number" placeholder="Total Debt" value="${amount}" onchange="updateField('debts', '${item.id}', 'amount', parseFloat(this.value))">
                            <input type="number" placeholder="Monthly Payment" value="${monthlyPayment}" onchange="updateField('debts', '${item.id}', 'monthlyPayment', parseFloat(this.value))">
                        </div>
                        ${monthlyPayment > 0 ? `<div class="debt-info">⏱️ ${months} months remaining (before next payment)</div>` : `<div class="debt-info">⚠️ Set a monthly payment to see forecast</div>`}
                        <button class="btn-delete" onclick="deleteItem('debts', '${item.id}')" style="width: 100%; margin-top: 10px;">🗑️ Delete</button>
                    </div>
                `;
            }).join('');
            
            document.getElementById('debtsList').innerHTML = html;
        }

        function renderPurchases() { 
            const formatCurrency = (amount) => (amount || 0).toLocaleString('en-IN', {maximumFractionDigits: 0});
            
            // Sort purchases by date descending
            const sortedPurchases = appData.purchases.slice().sort((a, b) => {
                const dateA = a.date || '1900-01-01';
                const dateB = b.date || '1900-01-01';
                return dateB.localeCompare(dateA);
            });

            const html = sortedPurchases.map(item => `
                <div class="purchase-row">
                    <input type="text" value="${item.name}" onchange="updateField('purchases', '${item.id}', 'name', this.value)">
                    <input type="number" value="${item.amount}" onchange="updateField('purchases', '${item.id}', 'amount', parseFloat(this.value))">
                    <input type="date" value="${item.date}" onchange="updateField('purchases', '${item.id}', 'date', this.value)">
                    <button class="btn-delete" onclick="deleteItem('purchases', '${item.id}')">🗑️</button>
                </div>
            `).join('');
            
            document.getElementById('purchasesList').innerHTML = html + 
                `<div class="total-row" style="background: #e6e6fa;">
                    Total One-Time Purchases THIS MONTH 
                    <span>₹${formatCurrency(appData.currentMonthPurchasesTotal)}</span>
                </div>
                <div class="total-row">
                    Total All-Time Purchases 
                    <span>₹${formatCurrency(appData.totalAllTimePurchases)}</span>
                </div>`;
        }
        
        function renderCharts() {
            const { totalExpenses, totalInvestments, totalDebtPayment, totalIncomeRate } = appData;
            
            Object.values(chartInstances).forEach(chart => chart?.destroy());
            chartInstances = {};
            
            const recurringOutflow = totalExpenses + totalInvestments + totalDebtPayment;
            const forecastedRemaining = Math.max(totalIncomeRate - recurringOutflow, 0); 

            // Cash Flow Pie Chart (Shows recurring budget allocation)
            const ctx1 = document.getElementById('cashFlowChart');
            if (ctx1) {
                chartInstances.cashFlow = new Chart(ctx1, {
                    type: 'doughnut',
                    data: {
                        labels: ['Expenses (Recurring)', 'Investments', 'Debt Payments', 'Remaining (Forecast)'],
                        datasets: [{
                            data: [totalExpenses, totalInvestments, totalDebtPayment, forecastedRemaining],
                            backgroundColor: ['#f56565', '#4299e1', '#ed8936', '#48bb78']
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { position: 'bottom' }, title: {display: true, text: 'Monthly Recurring Budget Breakdown'} }
                    }
                });
            }
            
            // Breakdown Bar Chart (Shows total movement this month)
            const ctx2 = document.getElementById('breakdownChart');
            if (ctx2) {
                chartInstances.breakdown = new Chart(ctx2, {
                    type: 'bar',
                    data: {
                        labels: ['Income Rate', 'Total Outflow (This Month)'],
                        datasets: [{
                            label: 'Amount (₹)',
                            data: [totalIncomeRate, appData.totalOutflow],
                            backgroundColor: ['#48bb78', '#f56565']
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { display: true }, title: {display: true, text: 'Total Cash Flow Movement'} },
                        scales: { y: { beginAtZero: true } }
                    }
                });
            }
        }
        
        async function updateField(category, id, field, value) {
            const result = await fetchData('/api/update', 'POST', { 
                category, id, field, value: value.toString() 
            });
            if (result) await loadData();
        }
        
        async function deleteItem(category, id) {
            // Use a custom UI for confirmation instead of alert/confirm for better UX
            if (category === 'income' && id === 'inc2') {
                console.warn('The Current Account Balance item cannot be deleted.');
                return;
            }
            if (!window.confirm('Are you sure you want to permanently delete this item?')) return; 

            const result = await fetchData('/api/delete', 'POST', { category, id });
            if (result) await loadData();
        }
        
        async function addItem(category) {
            // Generate a temporary unique ID
            const id = 'item-' + Math.random().toString(36).substr(2, 9);
            const today = new Date().toISOString().slice(0, 10); 
            
            let item;
            if (category === 'debts') {
                item = { id, name: 'New Debt', amount: 0, monthlyPayment: 0 };
            } else if (category === 'purchases') { 
                item = { id, name: 'New Purchase', amount: 0, date: today }; 
            } else if (category === 'income') {
                item = { id, name: 'Other Monthly Income', amount: 0 }; 
            } else {
                item = { id, name: 'New Item', amount: 0 };
            }
            
            const result = await fetchData('/api/add', 'POST', { category, item });
            if (result) await loadData();
        }
        
        function switchTab(tabName) {
            document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById(tabName).classList.add('active');
            document.querySelector(`.tab-btn[onclick="switchTab('${tabName}')"]`).classList.add('active');
            
            if (tabName === 'overview') {
                 // Ensure charts render correctly after tab visibility changes
                 setTimeout(renderCharts, 100); 
            }
        }
        
        window.onload = loadData; 
    </script>
</body>
</html>
"""

# --- 9. APP RUNNER ---

if __name__ == '__main__':
    # Use 0.0.0.0 for hosting environments like Render/Gunicorn
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), debug=True)