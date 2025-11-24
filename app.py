from flask import Flask, render_template_string, request, jsonify
from datetime import datetime, timedelta
import calendar
import os
from functools import wraps
from pymongo import MongoClient
from bson.objectid import ObjectId
from dotenv import load_dotenv

# --- 1. CONFIGURATION & SETUP ---

# Load environment variables from .env file (for local testing only)
# In production environments like Render, variables are set directly.
load_dotenv() 

app = Flask(__name__)

# Mandatory Configuration (Load from Environment)
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/") # Fallback to local MongoDB
# NOTE: MONGO_DB_NAME must be explicitly set, e.g., 'financial_db'
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "financial_db") 
# Secret Key for API authorization (used for POST/PUT/DELETE)
SECRET_KEY = os.getenv("SECRET_KEY", "A_FALLBACK_SECRET_KEY_FOR_LOCAL_DEV") 
COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME", "user_data")

# Pass the secret key to the template for client-side authentication
app.jinja_env.globals['SECRET_KEY'] = SECRET_KEY 

# --- 2. DATABASE CONNECTION & INITIALIZATION ---

def get_mongo_collection():
    """Connects to MongoDB and returns the collection object."""
    try:
        # Connect to the client
        client = MongoClient(MONGO_URI)
        
        # Explicitly use MONGO_DB_NAME
        db = client[MONGO_DB_NAME]
        collection = db[COLLECTION_NAME]
        
        # Perform a quick check for initial data
        if collection.count_documents({}) == 0:
            print("--- MongoDB: Initializing default data ---")
            initial_data = {
                'income': [
                    {'id': str(ObjectId()), 'name': 'Monthly Salary', 'amount': 120000},
                    {'id': str(ObjectId()), 'name': 'Side Hustle', 'amount': 5000},
                ],
                'expenses': [
                    {'id': str(ObjectId()), 'name': 'Rent/Mortgage', 'amount': 25000},
                    {'id': str(ObjectId()), 'name': 'Groceries', 'amount': 8000},
                    {'id': str(ObjectId()), 'name': 'Utilities', 'amount': 3500},
                ],
                'investments': [
                    {'id': str(ObjectId()), 'name': 'Mutual Funds', 'amount': 20000},
                    {'id': str(ObjectId()), 'name': 'Stocks', 'amount': 15000},
                ],
                'debts': [
                    {'id': str(ObjectId()), 'name': 'Car Loan', 'amount': 150000, 'monthlyPayment': 5000},
                    {'id': str(ObjectId()), 'name': 'Credit Card', 'amount': 10000, 'monthlyPayment': 1000},
                ],
                'purchases': [
                    {'id': str(ObjectId()), 'name': 'New Laptop', 'amount': 85000, 'date': datetime.now().strftime("%Y-%m-%d")},
                ]
            }
            collection.insert_one({'_id': 'user_data', **initial_data})
            print("--- MongoDB: Default data inserted ---")

        return collection

    except Exception as e:
        print(f"MongoDB connection error: {e}")
        # Return None or raise an error depending on desired failure mode
        return None

# --- 3. HELPER FUNCTIONS & DECORATORS ---

def json_encoder(obj):
    """Custom JSON encoder for MongoDB types."""
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    # Handle the main data structure which uses 'id' (str)
    if isinstance(obj, dict) and '_id' in obj and obj['_id'] == 'user_data':
        # Remove the internal Mongo ID for clean client consumption
        obj.pop('_id') 
    return obj

def api_key_required(f):
    """Decorator to check for a valid SECRET_KEY in the request headers."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_key = request.headers.get('X-API-Key')
        if auth_key != SECRET_KEY:
            return jsonify({'error': 'Unauthorized: Invalid API Key'}), 401
        return f(*args, **kwargs)
    return decorated_function

# --- 4. API ROUTES (CRUD) ---

@app.route('/api/data', methods=['GET'])
def get_data():
    """Fetches all financial data from MongoDB."""
    collection = get_mongo_collection()
    if collection is None:
        return jsonify({'error': 'Database unavailable'}), 503
    
    # Fetch the single document for user data
    data_doc = collection.find_one({'_id': 'user_data'})
    
    if not data_doc:
         # Should not happen if initialization worked, but handles edge case
        return jsonify({
            'income': [], 'expenses': [], 'investments': [], 'debts': [], 'purchases': []
        }), 200

    # Clean up the document before sending
    data_doc.pop('_id')
    return jsonify(data_doc)

@app.route('/api/edit', methods=['POST'])
@api_key_required
def edit_item():
    """Updates a single field of an item within a category."""
    try:
        data = request.json
        category = data.get('category')
        item_id = data.get('id')
        field = data.get('field')
        value = data.get('value')

        if not all([category, item_id, field is not None, value is not None]):
            return jsonify({'error': 'Missing required fields'}), 400

        collection = get_mongo_collection()
        if collection is None:
            return jsonify({'error': 'Database unavailable'}), 503

        # Construct the query to find the item and the update operation
        # This uses the $set operator on the array element matching the ID
        update_field = f"{category}.$[elem].{field}"
        
        # Convert numeric values if necessary
        try:
            if field in ['amount', 'monthlyPayment']:
                value = float(value)
        except ValueError:
            # For non-numeric string, keep as string
            pass

        result = collection.update_one(
            {'_id': 'user_data'},
            {'$set': {update_field: value}},
            array_filters=[{'elem.id': item_id}] # Filter the specific array element
        )

        if result.matched_count == 0:
            return jsonify({'error': 'Item not found'}), 404

        return jsonify({'success': True, 'message': 'Item updated'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/add', methods=['POST'])
@api_key_required
def add_item():
    """Adds a new item to a category array."""
    try:
        data = request.json
        category = data.get('category')
        item = data.get('item')

        if not all([category, item]):
            return jsonify({'error': 'Missing required fields'}), 400

        collection = get_mongo_collection()
        if collection is None:
            return jsonify({'error': 'Database unavailable'}), 503

        # Ensure the item has an ObjectId as string for uniqueness
        if 'id' not in item or not item['id']:
             item['id'] = str(ObjectId())

        result = collection.update_one(
            {'_id': 'user_data'},
            {'$push': {category: item}}
        )

        if result.modified_count == 0:
            # Handle case where document exists but category array might be missing (create it)
            collection.update_one(
                {'_id': 'user_data'},
                {'$set': {category: [item]}},
                upsert=True
            )
        
        return jsonify({'success': True, 'message': 'Item added'}), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/delete', methods=['POST'])
@api_key_required
def delete_item():
    """Removes an item from a category array by ID."""
    try:
        data = request.json
        category = data.get('category')
        item_id = data.get('id')

        if not all([category, item_id]):
            return jsonify({'error': 'Missing required fields'}), 400

        collection = get_mongo_collection()
        if collection is None:
            return jsonify({'error': 'Database unavailable'}), 503

        # Use $pull to remove the item from the array where 'id' matches
        result = collection.update_one(
            {'_id': 'user_data'},
            {'$pull': {category: {'id': item_id}}}
        )

        if result.modified_count == 0:
            return jsonify({'error': 'Item not found or not deleted'}), 404

        return jsonify({'success': True, 'message': 'Item deleted'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- 5. MAIN ROUTE (SERVE HTML) ---

@app.route('/')
def index():
    """Renders the single-page HTML application."""
    # Use Flask's Jinja global to pass the secret key to the JavaScript.
    return render_template_string(HTML_TEMPLATE)

# --- 6. FLASK RUN COMMAND ---

if __name__ == '__main__':
    # Use host='0.0.0.0' for deployment readiness (e.g., Render)
    app.run(debug=True, host='0.0.0.0', port=os.getenv("PORT", 5000))


# --- 7. EMBEDDED HTML/CSS/JAVASCRIPT (Single-Page App) ---

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Financial Tracker Dashboard</title>
    <!-- Load Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        /* Custom scrollbar for aesthetics */
        body::-webkit-scrollbar { width: 8px; }
        body::-webkit-scrollbar-track { background: #f1f1f1; }
        body::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 10px; }
        body::-webkit-scrollbar-thumb:hover { background: #94a3b8; }
        
        /* Custom styles for the active tab/section */
        .section { display: none; }
        .section.active { display: block; }
        .tab-btn.active { 
            background-color: #3b82f6; /* blue-500 */
            color: white; 
            box-shadow: 0 4px 6px -1px rgba(59, 130, 246, 0.5), 0 2px 4px -1px rgba(59, 130, 246, 0.25);
        }
        .data-row:hover {
            background-color: #f3f4f6; /* gray-100 */
        }
    </style>
    <!-- Load Chart.js for visualizations -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body class="bg-gray-50 font-sans min-h-screen">

    <div id="loading-overlay" class="fixed inset-0 bg-gray-900 bg-opacity-75 flex items-center justify-center z-50 transition-opacity duration-300">
        <div class="animate-spin rounded-full h-16 w-16 border-t-4 border-b-4 border-blue-500"></div>
        <p class="ml-4 text-white text-lg">Loading Data...</p>
    </div>

    <div class="container mx-auto p-4 md:p-8">
        <header class="text-center mb-8">
            <h1 class="text-4xl font-extrabold text-gray-800">Financial Overview Dashboard</h1>
            <p class="text-gray-500 mt-2">Track income, expenses, investments, and debts in real-time.</p>
        </header>

        <!-- Tabs Navigation -->
        <nav class="flex justify-center mb-8 sticky top-0 bg-white p-3 rounded-xl shadow-lg z-10">
            <button class="tab-btn active px-4 py-2 mx-1 text-sm font-medium rounded-lg text-gray-600 hover:bg-blue-100 transition duration-150" onclick="switchTab('overview', this)">Overview</button>
            <button class="tab-btn px-4 py-2 mx-1 text-sm font-medium rounded-lg text-gray-600 hover:bg-blue-100 transition duration-150" onclick="switchTab('income', this)">Income</button>
            <button class="tab-btn px-4 py-2 mx-1 text-sm font-medium rounded-lg text-gray-600 hover:bg-blue-100 transition duration-150" onclick="switchTab('expenses', this)">Expenses</button>
            <button class="tab-btn px-4 py-2 mx-1 text-sm font-medium rounded-lg text-gray-600 hover:bg-blue-100 transition duration-150" onclick="switchTab('investments', this)">Investments</button>
            <button class="tab-btn px-4 py-2 mx-1 text-sm font-medium rounded-lg text-gray-600 hover:bg-blue-100 transition duration-150" onclick="switchTab('debts', this)">Debts</button>
            <button class="tab-btn px-4 py-2 mx-1 text-sm font-medium rounded-lg text-gray-600 hover:bg-blue-100 transition duration-150" onclick="switchTab('purchases', this)">Purchases</button>
        </nav>

        <!-- --- OVERVIEW SECTION --- -->
        <section id="overview" class="section active bg-white p-6 rounded-xl shadow-2xl transition duration-500 ease-in-out">
            <h2 class="text-2xl font-bold text-gray-700 mb-6 border-b pb-2">Financial Snapshot</h2>
            
            <!-- Summary Cards -->
            <div id="summary-cards" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
                <!-- Card 1: Net Monthly Income -->
                <div class="bg-green-50 border-l-4 border-green-500 rounded-lg p-4 shadow-md">
                    <p class="text-sm font-medium text-gray-500">Net Monthly Income</p>
                    <p id="net-income" class="text-2xl font-semibold text-green-700 mt-1">₹0</p>
                </div>
                <!-- Card 2: Remaining Funds (Monthly) -->
                <div class="bg-blue-50 border-l-4 border-blue-500 rounded-lg p-4 shadow-md">
                    <p class="text-sm font-medium text-gray-500">Remaining (Post-Investment)</p>
                    <p id="remaining-funds" class="text-2xl font-semibold text-blue-700 mt-1">₹0</p>
                </div>
                <!-- Card 3: Total Investments -->
                <div class="bg-yellow-50 border-l-4 border-yellow-500 rounded-lg p-4 shadow-md">
                    <p class="text-sm font-medium text-gray-500">Total Monthly Investment</p>
                    <p id="total-investments" class="text-2xl font-semibold text-yellow-700 mt-1">₹0</p>
                </div>
                <!-- Card 4: Total Monthly Debt Payment -->
                <div class="bg-red-50 border-l-4 border-red-500 rounded-lg p-4 shadow-md">
                    <p class="text-sm font-medium text-gray-500">Total Monthly Debt Payment</p>
                    <p id="total-debt-payment" class="text-2xl font-semibold text-red-700 mt-1">₹0</p>
                </div>
            </div>

            <!-- Charts -->
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
                <div class="bg-gray-50 p-4 rounded-lg shadow-inner">
                    <h3 class="text-xl font-semibold text-gray-700 mb-3">Monthly Allocation</h3>
                    <div class="h-64">
                         <canvas id="monthlyAllocationChart"></canvas>
                    </div>
                </div>
                <div class="bg-gray-50 p-4 rounded-lg shadow-inner">
                    <h3 class="text-xl font-semibold text-gray-700 mb-3">Debt vs. Purchase</h3>
                    <div class="h-64">
                         <canvas id="debtPurchaseChart"></canvas>
                    </div>
                </div>
            </div>
        </section>

        <!-- --- Data Sections (Dynamic) --- -->
        {% set categories = [
            ('income', 'Monthly Income Sources', ['name', 'amount']),
            ('expenses', 'Monthly Fixed Expenses', ['name', 'amount']),
            ('investments', 'Monthly Investments', ['name', 'amount']),
            ('debts', 'Outstanding Debts', ['name', 'amount', 'monthlyPayment']),
            ('purchases', 'Recent Big Purchases', ['name', 'amount', 'date'])
        ] %}
        
        {% for category, title, fields in categories %}
        <section id="{{ category }}" class="section bg-white p-6 rounded-xl shadow-2xl transition duration-500 ease-in-out">
            <h2 class="text-2xl font-bold text-gray-700 mb-4">{{ title }}</h2>
            
            <div class="overflow-x-auto shadow-md rounded-lg mb-4">
                <table class="min-w-full divide-y divide-gray-200">
                    <thead class="bg-gray-50">
                        <tr>
                            <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Name</th>
                            {% for field in fields %}
                                {% if field != 'name' %}
                                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{{ field | replace('amount', 'Amount (₹)') | replace('monthlyPayment', 'Monthly Payment (₹)') | replace('date', 'Date') }}</th>
                                {% endif %}
                            {% endfor %}
                            <th class="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Action</th>
                        </tr>
                    </thead>
                    <tbody id="{{ category }}-body" class="bg-white divide-y divide-gray-200">
                        <!-- Data rows will be injected here by JavaScript -->
                        <tr><td colspan="{{ fields | length + 1 }}" class="text-center py-4 text-gray-500">Loading...</td></tr>
                    </tbody>
                    <tfoot class="bg-gray-50">
                        <tr>
                            <td class="px-6 py-3 font-bold text-gray-700">Total</td>
                            {% for field in fields %}
                                {% if field != 'name' %}
                                    <td id="{{ category }}-total-{{ field }}" class="px-6 py-3 font-bold text-gray-700">₹0</td>
                                {% endif %}
                            {% endfor %}
                            <td class="px-6 py-3"></td>
                        </tr>
                    </tfoot>
                </table>
            </div>

            <button onclick="addItem('{{ category }}')" class="bg-blue-500 hover:bg-blue-600 text-white font-semibold py-2 px-4 rounded-lg transition duration-150 shadow-md flex items-center">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 mr-2" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 5a1 1 0 011 1v3h3a1 1 0 110 2h-3v3a1 1 0 11-2 0v-3H6a1 1 0 110-2h3V6a1 1 0 011-1z" clip-rule="evenodd" /></svg>
                Add New Item
            </button>
        </section>
        {% endfor %}

    </div>
    
    <!-- JavaScript Logic -->
    <script>
        // Use Flask injected secret key
        const API_KEY = '{{ SECRET_KEY }}';
        let financialData = { income: [], expenses: [], investments: [], debts: [], purchases: [] };
        
        // Chart Instances
        let monthlyAllocationChart, debtPurchaseChart;

        // --- Utility Functions ---

        /** Fetches data from API with error handling. */
        async function fetchData(url, method = 'GET', body = null) {
            try {
                const options = {
                    method: method,
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Key': API_KEY // Add API Key for write operations
                    },
                };
                if (body && (method === 'POST' || method === 'PUT')) {
                    options.body = JSON.stringify(body);
                }

                const response = await fetch(url, options);
                
                if (response.status === 401) {
                    throw new Error('Unauthorized: Invalid API Key. Cannot save data.');
                }
                if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
                }
                
                if (method === 'GET' || method === 'PUT') {
                    return response.json();
                }
                return true; // Success for POST/DELETE
                
            } catch (error) {
                console.error('API Call Failed:', error.message);
                alert('Data operation failed: ' + error.message);
                return null;
            }
        }

        /** Formats number as Indian Rupee (INR) currency. */
        function formatCurrency(number) {
            if (number === null || number === undefined) return '₹0';
            return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(number);
        }

        /** Debounce function to limit API calls during rapid editing. */
        function debounce(func, delay) {
            let timeout;
            return function(...args) {
                clearTimeout(timeout);
                timeout = setTimeout(() => func.apply(this, args), delay);
            };
        }

        const debouncedEdit = debounce(async (category, id, field, value) => {
             // Skip API call if value is clearly invalid for numeric fields
             if (['amount', 'monthlyPayment'].includes(field) && isNaN(parseFloat(value))) {
                console.warn(`Skipping save for invalid numeric value in ${field}: ${value}`);
                return;
             }
            
            await fetchData('/api/edit', 'POST', { category, id, field, value });
            // Reload data after successful edit for consistency
            await loadData(false);
        }, 500);

        // --- Core Application Logic ---

        /** Loads all data from the API and updates the UI. */
        async function loadData(showLoading = true) {
            if (showLoading) document.getElementById('loading-overlay').classList.remove('opacity-0', 'invisible');

            const data = await fetchData('/api/data');
            
            if (data) {
                financialData = data;
                renderSections();
                calculateOverview();
                renderCharts();
            }

            if (showLoading) {
                 // Delay fade out slightly to ensure charts render properly
                setTimeout(() => {
                    document.getElementById('loading-overlay').classList.add('opacity-0', 'invisible');
                }, 300);
            }
        }

        /** Renders the data tables for all categories. */
        function renderSections() {
            for (const category in financialData) {
                const tableBody = document.getElementById(`${category}-body`);
                if (!tableBody) continue;
                
                tableBody.innerHTML = '';
                let totalAmount = 0;
                let totalMonthlyPayment = 0;
                
                financialData[category].forEach(item => {
                    // Accumulate totals
                    if (item.amount) totalAmount += item.amount;
                    if (item.monthlyPayment) totalMonthlyPayment += item.monthlyPayment;
                    
                    const row = document.createElement('tr');
                    row.className = 'data-row hover:bg-gray-100 transition duration-100';
                    row.innerHTML = `
                        <!-- Name Field -->
                        <td class="px-6 py-3 whitespace-nowrap">
                            <input 
                                type="text" 
                                value="${item.name || ''}" 
                                class="w-full bg-transparent border-none focus:ring-0 focus:outline-none"
                                onchange="debouncedEdit('${category}', '${item.id}', 'name', this.value)"
                            />
                        </td>
                        
                        <!-- Amount Field -->
                        <td class="px-6 py-3 whitespace-nowrap">
                            <input 
                                type="number" 
                                value="${item.amount || 0}" 
                                class="w-full bg-transparent border-none focus:ring-0 focus:outline-none text-right"
                                onchange="debouncedEdit('${category}', '${item.id}', 'amount', parseFloat(this.value) || 0)"
                            />
                        </td>
                        
                        <!-- Optional Monthly Payment Field (only for debts) -->
                        ${category === 'debts' ? `
                            <td class="px-6 py-3 whitespace-nowrap">
                                <input 
                                    type="number" 
                                    value="${item.monthlyPayment || 0}" 
                                    class="w-full bg-transparent border-none focus:ring-0 focus:outline-none text-right"
                                    onchange="debouncedEdit('${category}', '${item.id}', 'monthlyPayment', parseFloat(this.value) || 0)"
                                />
                            </td>
                        ` : ''}
                         
                        <!-- Optional Date Field (only for purchases) -->
                         ${category === 'purchases' ? `
                            <td class="px-6 py-3 whitespace-nowrap">
                                <input 
                                    type="date" 
                                    value="${item.date || new Date().toISOString().slice(0, 10)}" 
                                    class="w-full bg-transparent border-none focus:ring-0 focus:outline-none text-right"
                                    onchange="debouncedEdit('${category}', '${item.id}', 'date', this.value)"
                                />
                            </td>
                        ` : ''}

                        <!-- Action Button -->
                        <td class="px-6 py-3 whitespace-nowrap text-right text-sm font-medium">
                            <button onclick="deleteItem('${category}', '${item.id}')" class="text-red-600 hover:text-red-900 transition duration-150 p-2 rounded-full hover:bg-red-100">
                                <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd" /></svg>
                            </button>
                        </td>
                    `;
                    tableBody.appendChild(row);
                });

                // Update Totals in Footer
                document.getElementById(`${category}-total-amount`).textContent = formatCurrency(totalAmount);
                if (category === 'debts') {
                    const totalMonthlyPaymentEl = document.getElementById(`${category}-total-monthlyPayment`);
                    if (totalMonthlyPaymentEl) {
                         totalMonthlyPaymentEl.textContent = formatCurrency(totalMonthlyPayment);
                    }
                }
            }
        }

        /** Calculates and displays the financial overview metrics. */
        function calculateOverview() {
            const totalIncome = financialData.income.reduce((sum, item) => sum + (item.amount || 0), 0);
            const totalExpenses = financialData.expenses.reduce((sum, item) => sum + (item.amount || 0), 0);
            const totalInvestments = financialData.investments.reduce((sum, item) => sum + (item.amount || 0), 0);
            const totalDebtPayment = financialData.debts.reduce((sum, item) => sum + (item.monthlyPayment || 0), 0);

            // Calculate Metrics
            const netMonthlyIncome = totalIncome - totalExpenses;
            const remainingFunds = netMonthlyIncome - totalInvestments - totalDebtPayment;

            // Update Cards
            document.getElementById('net-income').textContent = formatCurrency(netMonthlyIncome);
            document.getElementById('remaining-funds').textContent = formatCurrency(remainingFunds);
            document.getElementById('total-investments').textContent = formatCurrency(totalInvestments);
            document.getElementById('total-debt-payment').textContent = formatCurrency(totalDebtPayment);

            // Update card colors based on value
            const updateCardColor = (id, value) => {
                const card = document.getElementById(id).parentElement;
                card.classList.remove('bg-green-50', 'bg-red-50', 'border-green-500', 'border-red-500');
                card.classList.add(value >= 0 ? 'bg-green-50' : 'bg-red-50');
                card.classList.add(value >= 0 ? 'border-green-500' : 'border-red-500');
                document.getElementById(id).classList.remove('text-green-700', 'text-red-700');
                document.getElementById(id).classList.add(value >= 0 ? 'text-green-700' : 'text-red-700');
            }
            updateCardColor('net-income', netMonthlyIncome);
            updateCardColor('remaining-funds', remainingFunds);
        }

        /** Renders the Chart.js visualizations. */
        function renderCharts() {
            const totalExpenses = financialData.expenses.reduce((sum, item) => sum + (item.amount || 0), 0);
            const totalInvestments = financialData.investments.reduce((sum, item) => sum + (item.amount || 0), 0);
            const totalDebtPayment = financialData.debts.reduce((sum, item) => sum + (item.monthlyPayment || 0), 0);
            const totalIncome = financialData.income.reduce((sum, item) => sum + (item.amount || 0), 0);
            const netMonthlyIncome = totalIncome - totalExpenses;
            const remainingFunds = netMonthlyIncome - totalInvestments - totalDebtPayment;

            // 1. Monthly Allocation Chart (Doughnut)
            if (monthlyAllocationChart) monthlyAllocationChart.destroy();
            const allocationData = {
                labels: ['Expenses', 'Investments', 'Debt Payments', 'Remaining Funds'],
                datasets: [{
                    data: [totalExpenses, totalInvestments, totalDebtPayment, Math.max(0, remainingFunds)],
                    backgroundColor: ['#ef4444', '#f59e0b', '#60a5fa', '#10b981'], // Red, Yellow, Blue, Green
                    hoverOffset: 4
                }]
            };

            monthlyAllocationChart = new Chart(
                document.getElementById('monthlyAllocationChart'),
                {
                    type: 'doughnut',
                    data: allocationData,
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: {
                                position: 'right',
                            },
                            title: {
                                display: false,
                            }
                        }
                    }
                }
            );

            // 2. Debt vs. Purchase Chart (Bar)
            const totalPurchases = financialData.purchases.reduce((sum, item) => sum + (item.amount || 0), 0);
            const totalDebt = financialData.debts.reduce((sum, item) => sum + (item.amount || 0), 0);

            if (debtPurchaseChart) debtPurchaseChart.destroy();
            const debtPurchaseData = {
                labels: ['Total Outstanding Debt', 'Total Big Purchases'],
                datasets: [{
                    label: 'Amount (₹)',
                    data: [totalDebt, totalPurchases],
                    backgroundColor: ['#f87171', '#34d399'], // Red, Green
                    borderColor: ['#b91c1c', '#047857'],
                    borderWidth: 1
                }]
            };

            debtPurchaseChart = new Chart(
                document.getElementById('debtPurchaseChart'),
                {
                    type: 'bar',
                    data: debtPurchaseData,
                    options: {
                         responsive: true,
                         maintainAspectRatio: false,
                         scales: {
                            y: {
                                beginAtZero: true
                            }
                        }
                    }
                }
            );
        }

        /** Handles deletion of an item. */
        async function deleteItem(category, id) {
            if (!confirm('Are you sure you want to delete this item?')) return;
            
            const result = await fetchData('/api/delete', 'POST', { category, id });
            if (result) await loadData();
        }

        /** Handles adding a new item with default values. */
        async function addItem(category) {
            // Generate a temporary unique ID (Note: Backend also ensures uniqueness)
            const id = 'item-' + Math.random().toString(36).substr(2, 9);
            const today = new Date().toISOString().slice(0, 10); 
            
            let item;
            if (category === 'debts') {
                item = { id, name: 'New Debt', amount: 0, monthlyPayment: 0 };
            } else if (category === 'purchases') { 
                item = { id, name: 'New Purchase', amount: 0, date: today }; 
            } else if (category === 'income') {
                item = { id, name: 'Other Monthly Income', amount: 0 }; 
            } else if (category === 'expenses') {
                item = { id, name: 'New Expense', amount: 0 }; 
            } else if (category === 'investments') {
                item = { id, name: 'New Investment', amount: 0 }; 
            } else {
                item = { id, name: 'New Item', amount: 0 };
            }
            
            const result = await fetchData('/api/add', 'POST', { category, item });
            if (result) await loadData();
        }
        
        /** Controls the display of content sections. */
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
        
        // Initial load on window load
        window.onload = loadData; 
    </script>
</body>
</html>
"""