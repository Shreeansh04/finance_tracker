from flask import Flask, render_template_string, request, jsonify
from datetime import datetime, timedelta
import calendar
from pymongo import MongoClient
from dotenv import load_dotenv
import os
from functools import wraps
from bson.objectid import ObjectId

# Load environment variables from .env file (for local testing)
load_dotenv() 

app = Flask(__name__)

# --- 1. CONFIGURATION (LOAD FROM ENVIRONMENT) ---
# NOTE: Replace 'mongodb+srv://...' with your actual MongoDB Atlas connection string
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://user:pass@cluster.mongodb.net/financial_db?retryWrites=true&w=majority")
# EXPLICITLY set the database name. If it's not in the URI, this will be used.
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "financial_db") 
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
        
        # Explicitly use MONGO_DB_NAME to define the database
        db = client.get_database(MONGO_DB_NAME) 
        
        # A simple operation to check connection without retrieving data
        db.command('ping') 
        
        return db[COLLECTION_NAME]
    except Exception as e:
        # Increased visibility for the critical error
        print(f"!!! FATAL: Could not connect to MongoDB. Check MONGO_URI and MONGO_DB_NAME: {e}")
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
        'one_time_inflow': [
            {'id': 'oti1', 'name': 'Stock Sale Profit', 'amount': 12000, 'date': today},
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
        
    existing_doc = collection.find_one({}, {'_id': 1})
    
    # We must ensure we don't save the temporary _id if it's not the MongoDB ObjectId
    save_doc = data_to_save.copy()
    save_doc.pop('_id', None) 
    
    if existing_doc:
        # Use the existing _id to update the document
        collection.replace_one({'_id': existing_doc['_id']}, save_doc, upsert=True)
    else:
        # No existing document found, insert a new one
        collection.insert_one(save_doc)


# --- HELPER FUNCTIONS FOR BALANCE MANAGEMENT ---

def find_balance_index(data):
    """Safely finds the index of the 'Current Account Balance' item (inc2)."""
    for i, item in enumerate(data.get('income', [])):
        if item.get('id') == 'inc2':
            return i
    return -1

def update_balance(data, category, amount_change):
    """
    Updates the 'Current Account Balance' based on a transaction's change amount.
    amount_change is the NET change: (New Amount - Old Amount) or (Amount to be added/subtracted).
    """
    balance_idx = find_balance_index(data)
    if balance_idx == -1:
        print("Error: 'Current Account Balance' item (inc2) not found.")
        return
    
    if category == 'one_time_inflow':
        # Inflow adds to the balance (a positive change adds, a negative change subtracts)
        data['income'][balance_idx]['amount'] += amount_change
    elif category == 'purchases':
        # Purchases subtract from the balance (a positive change subtracts, a negative change adds)
        data['income'][balance_idx]['amount'] -= amount_change

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
    NOTE: One-time inflow/purchases are handled immediately in the API routes.
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
    try:
        next_month_start = current_date.replace(day=1) + timedelta(days=32)
        last_day_of_month = next_month_start.replace(day=1) - timedelta(days=1)
    except ValueError:
        last_day_of_month = current_date 
        
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
        # Call save_data if modifications were made
        save_data(data)
        return True
    
    return False

def load_data():
    """Fetches data from MongoDB or creates a new document."""
    global data
    collection = get_mongo_collection()
    
    if collection is None:
        # Fallback to in-memory default if DB fails
        print("Warning: Using in-memory default data. Persistence disabled.") 
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
        # For safety/cleanliness, ensure the BSON ID is not in the working data
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

    # --- One-Time Inflow Calculation ---
    current_month_inflow_total = 0
    total_all_time_inflow = 0
    
    for item in data.get('one_time_inflow', []):
        try:
            amount = float(item.get('amount', 0))
            total_all_time_inflow += amount
            inflow_date = datetime.strptime(item.get('date', '1900-01-01'), '%Y-%m-%d')
            
            if (inflow_date.year, inflow_date.month) == current_month_year:
                current_month_inflow_total += amount
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
    total_inflow_for_stats = total_income_rate + current_month_inflow_total 
    
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
        
        'currentMonthInflowTotal': current_month_inflow_total,
        'totalAllTimeInflow': total_all_time_inflow,
        'totalInflowForStats': total_inflow_for_stats, 
        
        'totalOutflow': total_outflow_for_stats, 
        'remainingBalance': remaining_after_recurring, 
        'isPositive': remaining_after_recurring >= 0
    }


# --- 7. FLASK ROUTES / API ENDPOINTS ---

@app.route('/')
def index():
    # Pass the updated HTML template
    return render_template_string(HTML_TEMPLATE_UPDATED)

@app.route('/api/data')
def get_data():
    # Recalculate totals just before sending
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
        # Generate a temporary unique ID (must be done on client side for immediate UI update, 
        # but also safe to generate here if it wasn't provided)
        if 'id' not in item:
            item['id'] = 'item-' + os.urandom(4).hex()
            
        # Ensure amount is treated as float/number
        try:
            amount = float(item.get('amount', 0))
            item['amount'] = amount
        except (TypeError, ValueError):
            # If amount is not a valid number, treat as 0
            amount = 0.0
            item['amount'] = 0.0
            
        data[category].append(item)
        
        # --- NEW LOGIC: Update Balance Immediately ---
        if category in ['one_time_inflow', 'purchases']:
            # For new items, amount_change is simply the item's amount.
            update_balance(data, category, amount)
        # -------------------------------------------
        
        save_data(data)
    
    # Recalculate totals and return the updated state
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
                old_amount = float(item.get('amount', 0))
                
                # Handle numeric fields
                if field in ['amount', 'monthlyPayment']:
                    try:
                        # Convert value back to float/int for storage
                        new_amount = float(value)
                        item[field] = new_amount
                        data_modified = True
                    except ValueError:
                        print(f"Warning: Invalid number value '{value}' for field '{field}'")
                        new_amount = old_amount # Keep old amount for balance change calculation if parsing failed
                # Handle all other fields (name, date)
                else:
                    item[field] = value
                    data_modified = True
                    new_amount = old_amount # Amount didn't change

                # --- NEW LOGIC: Update Balance Immediately on amount change ---
                if data_modified and category in ['one_time_inflow', 'purchases'] and field == 'amount':
                    change = new_amount - old_amount
                    
                    # Update balance: change is the net difference (new - old).
                    update_balance(data, category, change)
                # -------------------------------------------------------------
                
                break
        
        if data_modified:
            save_data(data)
    
    # Recalculate totals and return the updated state
    totals = calculate_totals()
    return jsonify({'success': True, **totals})

@app.route('/api/delete', methods=['POST'])
@requires_auth # Protected
def delete_item():
    global data
    req = request.json
    category = req.get('category')
    item_id = req.get('id')
    
    data_modified = False
    deleted_item = None
    
    if category in data and isinstance(data[category], list):
        # Find the item BEFORE filtering it out
        deleted_item = next((item for item in data.get(category, []) if item.get('id') == item_id), None)
        
        # Filter the list (the actual deletion)
        data[category] = [item for item in data[category] if item.get('id') != item_id]
        
        if deleted_item:
            data_modified = True
            
            # --- NEW LOGIC: Undo Balance Change Immediately ---
            if category in ['one_time_inflow', 'purchases']:
                try:
                    amount = float(deleted_item.get('amount', 0))
                except (TypeError, ValueError):
                    amount = 0.0
                    
                # Deleting means the net change is the negative of the original amount's effect.
                # E.g., deleting an inflow of +1000 is a change of -1000.
                if category == 'one_time_inflow':
                    update_balance(data, category, -amount)
                elif category == 'purchases':
                    # Deleting a purchase means the net change is positive (undoing the debit)
                    # Since update_balance subtracts for purchases, we pass -amount: -(-amount) = +amount
                    update_balance(data, category, -amount)
            # ------------------------------------------------
            
            save_data(data)
    
    # Recalculate totals and return the updated state
    totals = calculate_totals()
    return jsonify({'success': True, **totals})


# --- 8. HTML TEMPLATE DEFINITION (Frontend Logic Update for Auth) ---
# NOTE: The HTML_TEMPLATE_UPDATED includes the new 'One-Time Inflow' tab, section, and rendering logic.
HTML_TEMPLATE_UPDATED = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Financial Dashboard</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/3.9.1/chart.min.js"></script>
    <style>
        /* Base styles and reset */
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        /* Body and main container styling */
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
        
        /* Header styling */
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
        
        /* Cards and grid styling */
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
            transition: transform 0.3s ease;
        }

        .card:hover {
            transform: translateY(-3px);
            box-shadow: 0 8px 25px rgba(0,0,0,0.12);
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
        
        /* Tabs styling */
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
        
        /* Section styling */
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
        
        /* List item rows styling */
        .item-row {
            display: grid;
            grid-template-columns: 2fr 1fr 50px;
            gap: 15px;
            padding: 15px;
            border-bottom: 1px solid #f0f0f0;
            align-items: center;
            transition: all 0.2s;
        }
        
        .inflow-purchase-row { /* New class for date/amount/name rows */
            grid-template-columns: 2fr 1fr 1fr 50px; 
        }

        .item-row:hover {
            background: #f9f9f9;
        }
        
        .item-row:last-child {
            border-bottom: none;
        }
        
        .item-row input, .debt-item input {
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 1em;
            transition: all 0.2s;
        }
        
        .item-row input:focus, .debt-item input:focus {
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        
        /* Buttons */
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
        
        /* Total rows */
        .total-row {
            padding: 15px;
            background: #f0f0ff; /* Light purple tint for totals */
            border-radius: 8px;
            font-weight: bold;
            font-size: 1.1em;
            margin-top: 20px;
            display: flex;
            justify-content: space-between;
            border: 1px solid #ddd;
        }
        
        /* Debt specific styling */
        .debt-item {
            background: #f9f9f9;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 15px;
            border-left: 4px solid #f5a622;
        }
        
        .debt-info {
            color: #11998e;
            font-size: 0.9em;
            margin-top: 10px;
            padding: 10px;
            background: #f0fffe;
            border-radius: 6px;
        }
        
        /* Chart styling */
        .chart-container {
            position: relative;
            height: 300px;
            margin-bottom: 30px;
        }
        
        /* Footer styling */
        footer {
            text-align: center;
            color: rgba(255,255,255,0.7);
            margin-top: 40px;
            padding: 20px;
        }

        /* Responsive adjustments */
        @media (max-width: 768px) {
            .cards-grid {
                grid-template-columns: 1fr;
            }
            .item-row, .inflow-purchase-row {
                grid-template-columns: 1fr 1fr 50px;
            }
            .inflow-purchase-row input:nth-child(3) { /* date field */
                grid-column: 1 / span 2;
            }
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
            <button class="tab-btn active" onclick="switchTab('overview', this)">📊 Overview</button>
            <button class="tab-btn" onclick="switchTab('income', this)">💵 Income (Recurring)</button>
            <button class="tab-btn" onclick="switchTab('one_time_inflow', this)">➕ One-Time Inflow</button>
            <button class="tab-btn" onclick="switchTab('expenses', this)">💸 Expenses</button>
            <button class="tab-btn" onclick="switchTab('investments', this)">📈 Investments</button>
            <button class="tab-btn" onclick="switchTab('debts', this)">💳 Debts</button>
            <button class="tab-btn" onclick="switchTab('purchases', this)">🛒 Purchases</button> 
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
            <h2>💵 Recurring Income Sources</h2>
            <div id="incomeList"></div>
            <button class="btn-add" onclick="addItem('income')">➕ Add Income Source</button>
        </div>
        
        <div id="one_time_inflow" class="section">
            <h2>➕ One-Time Inflow (e.g., Stock Sale, Bonus)</h2>
            <div id="oneTimeInflowList"></div>
            <button class="btn-add" onclick="addItem('one_time_inflow')">➕ Record New Inflow</button>
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
                     // Using console.error instead of alert/confirm
                     console.error('Authentication failed. Check your AUTH_KEY and deployment settings.');
                     return null;
                }
                if (!response.ok) {
                    const errorBody = await response.json().catch(() => ({}));
                    console.error('HTTP error! status: ' + response.status, errorBody);
                    // Throw to ensure the calling async function knows to stop
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
            renderOneTimeInflow(); 
            renderExpenses();
            renderInvestments();
            renderDebts();
            renderPurchases(); 
            renderCharts();
        }
        
        function renderStats() {
            // Updated stats to reflect total inflow for stats
            const { currentBalance, totalIncomeRate, totalDebt, remainingBalance, isPositive, totalOutflow, totalInflowForStats, currentMonthPurchasesTotal, currentMonthInflowTotal } = appData;
            
            const formatCurrency = (amount) => (amount || 0).toLocaleString('en-IN', {maximumFractionDigits: 0});

            const statsHTML = '\
                <div class="card neutral">\
                    <div class="card-label">Current Account Balance</div>\
                    <div class="card-value">₹' + formatCurrency(currentBalance) + '</div>\
                    <div class="card-status">Cash available for spending (Updated with One-Time Transactions)</div>\
                </div>\
                <div class="card ' + (isPositive ? 'positive' : 'negative') + '">\
                    <div class="card-label">Monthly Forecast Remaining (Recurring)</div>\
                    <div class="card-value">₹' + formatCurrency(remainingBalance) + '</div>\
                    <div class="card-status">Income Rate (' + formatCurrency(totalIncomeRate) + ') - Recurring Outflow</div>\
                </div>\
                <div class="card positive">\
                    <div class="card-label">Total Inflow (This Month)</div>\
                    <div class="card-value">₹' + formatCurrency(totalInflowForStats) + '</div>\
                    <div class="card-status">Recurring Income + One-Time Inflow (₹' + formatCurrency(currentMonthInflowTotal) + ')</div>\
                </div>\
                <div class="card warning">\
                    <div class="card-label">Total Outflow (This Month)</div>\
                    <div class="card-value">₹' + formatCurrency(totalOutflow) + '</div>\
                    <div class="card-status">Total Recurring & Purchases (₹' + formatCurrency(currentMonthPurchasesTotal) + ')</div>\
                </div>\
                <div class="card negative">\
                    <div class="card-label">Total Debt Outstanding</div>\
                    <div class="card-value">₹' + formatCurrency(totalDebt) + '</div>\
                    <div class="card-status">Principal amount before next payment</div>\
                </div>\
            ';
            
            document.getElementById('statsCards').innerHTML = statsHTML;
        }
        
        function renderIncome() {
            const formatCurrency = (amount) => (amount || 0).toLocaleString('en-IN', {maximumFractionDigits: 0});
            
            const html = appData.income.map(item => '\
                <div class="item-row" style="' + (item.id === 'inc2' ? 'background: #f0f0ff;' : '') + '">\
                    <input type="text" value="' + item.name + '" onchange="updateField(\'income\', \'' + item.id + '\', \'name\', this.value)" ' + (item.id === 'inc2' ? 'readonly' : '') + '>\
                    <input type="number" value="' + item.amount + '" onchange="updateField(\'income\', \'' + item.id + '\', \'amount\', parseFloat(this.value))" ' + (item.id === 'inc2' ? 'readonly' : '') + '>\
                    ' + (item.id === 'inc2' ? '<button class="btn-delete" disabled>🔒</button>' : '<button class="btn-delete" onclick="deleteItem(\'income\', \'' + item.id + '\')">🗑️</button>') + '\
                </div>\
            ').join('');
            
            document.getElementById('incomeList').innerHTML = html + '<div class="total-row">Total Recurring Income Rate <span>₹' + formatCurrency(appData.totalIncomeRate) + '</span></div>';
        }
        
        // NEW RENDER FUNCTION: One-Time Inflow
        function renderOneTimeInflow() { 
            const formatCurrency = (amount) => (amount || 0).toLocaleString('en-IN', {maximumFractionDigits: 0});
            
            // Sort inflow by date descending
            const sortedInflow = appData.one_time_inflow.slice().sort((a, b) => {
                const dateA = a.date || '1900-01-01';
                const dateB = b.date || '1900-01-01';
                return dateB.localeCompare(dateA);
            });

            const html = sortedInflow.map(item => '\
                <div class="item-row inflow-purchase-row">\
                    <input type="text" value="' + item.name + '" onchange="updateField(\'one_time_inflow\', \'' + item.id + '\', \'name\', this.value)">\
                    <input type="number" value="' + item.amount + '" onchange="updateField(\'one_time_inflow\', \'' + item.id + '\', \'amount\', parseFloat(this.value))">\
                    <input type="date" value="' + item.date + '" onchange="updateField(\'one_time_inflow\', \'' + item.id + '\', \'date\', this.value)">\
                    <button class="btn-delete" onclick="deleteItem(\'one_time_inflow\', \'' + item.id + '\')">🗑️</button>\
                </div>\
            ').join('');
            
            document.getElementById('oneTimeInflowList').innerHTML = html + '\
                <div class="total-row" style="background: #e6e6fa;">\
                    Total One-Time Inflow THIS MONTH \
                    <span>₹' + formatCurrency(appData.currentMonthInflowTotal) + '</span>\
                </div>\
                <div class="total-row">\
                    Total All-Time Inflow \
                    <span>₹' + formatCurrency(appData.totalAllTimeInflow) + '</span>\
                </div>';
        }
        // END NEW RENDER FUNCTION

        function renderExpenses() {
            const formatCurrency = (amount) => (amount || 0).toLocaleString('en-IN', {maximumFractionDigits: 0});
            const html = appData.expenses.map(item => '\
                <div class="item-row">\
                    <input type="text" value="' + item.name + '" onchange="updateField(\'expenses\', \'' + item.id + '\', \'name\', this.value)">\
                    <input type="number" value="' + item.amount + '" onchange="updateField(\'expenses\', \'' + item.id + '\', \'amount\', parseFloat(this.value))">\
                    <button class="btn-delete" onclick="deleteItem(\'expenses\', \'' + item.id + '\')">🗑️</button>\
                </div>\
            ').join('');
            
            document.getElementById('expensesList').innerHTML = html + '<div class="total-row">Total Expenses <span>₹' + formatCurrency(appData.totalExpenses) + '</span></div>';
        }
        
        function renderInvestments() {
            const formatCurrency = (amount) => (amount || 0).toLocaleString('en-IN', {maximumFractionDigits: 0});
            const html = appData.investments.map(item => '\
                <div class="item-row">\
                    <input type="text" value="' + item.name + '" onchange="updateField(\'investments\', \'' + item.id + '\', \'name\', this.value)">\
                    <input type="number" value="' + item.amount + '" onchange="updateField(\'investments\', \'' + item.id + '\', \'amount\', parseFloat(this.value))">\
                    <button class="btn-delete" onclick="deleteItem(\'investments\', \'' + item.id + '\')">🗑️</button>\
                </div>\
            ').join('');
            
            document.getElementById('investmentsList').innerHTML = html + '<div class="total-row">Total Investments <span>₹' + formatCurrency(appData.totalInvestments) + '</span></div>';
        }
        
        function renderDebts() {
            const html = appData.debts.map(item => {
                const amount = parseFloat(item.amount) || 0;
                const monthlyPayment = parseFloat(item.monthlyPayment) || 0;

                const months = monthlyPayment > 0 ? (amount / monthlyPayment).toFixed(1) : '∞';
                
                return '\
                    <div class="debt-item">\
                        <input type="text" placeholder="Creditor Name" value="' + item.name + '" onchange="updateField(\'debts\', \'' + item.id + '\', \'name\', this.value)">\
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">\
                            <input type="number" placeholder="Total Debt" value="' + amount + '" onchange="updateField(\'debts\', \'' + item.id + '\', \'amount\', parseFloat(this.value))">\
                            <input type="number" placeholder="Monthly Payment" value="' + monthlyPayment + '" onchange="updateField(\'debts\', \'' + item.id + '\', \'monthlyPayment\', parseFloat(this.value))">\
                        </div>\
                        ' + (monthlyPayment > 0 ? '<div class="debt-info">⏱️ ' + months + ' months remaining (before next payment)</div>' : '<div class="debt-info">⚠️ Set a monthly payment to see forecast</div>') + '\
                        <button class="btn-delete" onclick="deleteItem(\'debts\', \'' + item.id + '\')" style="width: 100%; margin-top: 10px;">🗑️ Delete</button>\
                    </div>\
                ';
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

            const html = sortedPurchases.map(item => '\
                <div class="item-row inflow-purchase-row">\
                    <input type="text" value="' + item.name + '" onchange="updateField(\'purchases\', \'' + item.id + '\', \'name\', this.value)">\
                    <input type="number" value="' + item.amount + '" onchange="updateField(\'purchases\', \'' + item.id + '\', \'amount\', parseFloat(this.value))">\
                    <input type="date" value="' + item.date + '" onchange="updateField(\'purchases\', \'' + item.id + '\', \'date\', this.value)">\
                    <button class="btn-delete" onclick="deleteItem(\'purchases\', \'' + item.id + '\')">🗑️</button>\
                </div>\
            ').join('');
            
            document.getElementById('purchasesList').innerHTML = html + '\
                <div class="total-row" style="background: #e6e6fa;">\
                    Total One-Time Purchases THIS MONTH \
                    <span>₹' + formatCurrency(appData.currentMonthPurchasesTotal) + '</span>\
                </div>\
                <div class="total-row">\
                    Total All-Time Purchases \
                    <span>₹' + formatCurrency(appData.totalAllTimePurchases) + '</span>\
                </div>';
        }
        
        function renderCharts() {
            const { totalExpenses, totalInvestments, totalDebtPayment, totalIncomeRate, totalOutflow, totalInflowForStats } = appData;
            
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
            
            // Breakdown Bar Chart (Shows total movement this month - Updated to use new stats)
            const ctx2 = document.getElementById('breakdownChart');
            if (ctx2) {
                chartInstances.breakdown = new Chart(ctx2, {
                    type: 'bar',
                    data: {
                        labels: ['Total Inflow (This Month)', 'Total Outflow (This Month)'],
                        datasets: [{
                            label: 'Amount (₹)',
                            data: [totalInflowForStats, totalOutflow], // Use updated stats
                            backgroundColor: ['#48bb78', '#f56565']
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { display: true }, title: {display: true, text: 'Total Cash Flow Movement (This Month)'} },
                        scales: { y: { beginAtZero: true } }
                    }
                });
            }
        }
        
        async function updateField(category, id, field, value) {
            // Note: value is sent as string to backend, where it's parsed to float if needed.
            const result = await fetchData('/api/update', 'POST', { 
                category, id, field, value: value.toString() 
            });
            if (result) await loadData();
        }
        
        async function deleteItem(category, id) {
            // IMPORTANT: Replacing window.confirm() with a console warning as alert/confirm is forbidden
            if (category === 'income' && id === 'inc2') {
                console.warn('The Current Account Balance item cannot be deleted.');
                return;
            }
            
            // In a real application, replace this with a custom modal.
            console.warn('Action Warning: Attempting to delete item: ' + id + ' from ' + category);
            
            const result = await fetchData('/api/delete', 'POST', { category, id });
            if (result) await loadData();
        }
        
        async function addItem(category) {
            // Generate a temporary unique ID (Note: Backend also ensures uniqueness)
            const id = 'item-' + Math.random().toString(36).substr(2, 9);
            const today = new Date().toISOString().slice(0, 10); 
            
            let item;
            if (category === 'debts') {
                item = { id, name: 'New Debt', amount: 0, monthlyPayment: 0 };
            } else if (category === 'purchases') { 
                item = { id, name: 'New Purchase', amount: 0, date: today }; 
            } else if (category === 'one_time_inflow') { 
                item = { id, name: 'New Inflow', amount: 0, date: today };
            } else if (category === 'income') {
                item = { id, name: 'Other Monthly Income', amount: 0 }; 
            } else {
                item = { id, name: 'New Item', amount: 0 };
            }
            
            const result = await fetchData('/api/add', 'POST', { category, item });
            if (result) await loadData();
        }
        
        function switchTab(tabName, element) {
            document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById(tabName).classList.add('active');
            element.classList.add('active');
            
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