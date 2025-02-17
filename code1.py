# app.py
import os
import re
import json
import openai
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template
from pymongo import MongoClient
from flask_cors import CORS
from bson import json_util

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configuration
CONFIG = {
    "database": {
        "type": "mongodb",
        "uri": os.getenv("MONGO_URI", "mongodb://localhost:27017"),
        "name": os.getenv("DB_NAME", "study_abroad_crm")
    },
    "openai": {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "api_key": os.getenv("OPENAI_KEY_WOXOX")
    }
}

# MongoDB Adapter
class MongoDBAdapter:
    def __init__(self, config):
        self.client = MongoClient(config['uri'])
        self.db = self.client[config['name']]
        
    def execute_query(self, collection_name, query, operation='find'):
        try:
            collection = self.db[collection_name]
            
            if operation == 'count':
                return collection.count_documents(query.get('filter', {}))
                
            elif operation == 'find':
                return list(collection.find(
                    query.get('filter', {}),
                    {k: v for k, v in query.get('projection', {}).items() if v}
                ).limit(query.get('limit', 0)))
                
            elif operation == 'aggregate':
                return list(collection.aggregate(query.get('pipeline', [])))
                
            return {"error": "Invalid operation"}
            
        except Exception as e:
            return {"error": str(e)}
# Initialize MongoDB Adapter
mongo_adapter = MongoDBAdapter(CONFIG['database'])

# Security Middleware
def sanitize_input(user_query):
    """Prevent NoSQL injection and malicious operators"""
    disallowed_operators = ['$where', '$eval', '$function', '$accumulator']
    pattern = r'\$(' + '|'.join(disallowed_operators) + r')\b'
    if re.search(pattern, json.dumps(user_query)):
        raise ValueError("Disallowed MongoDB operator detected")
    return user_query

# OpenAI Service
class QueryGenerator:
    def __init__(self, config):
        self.client = openai.OpenAI(api_key=config['openai']['api_key'])
        self.model = config['openai']['model']
        
        # Load schema from separate file
        with open('schema_config.json') as f:
            self.schema = json.load(f)

    def generate_query(self, user_query):
        try:
            prompt = self._build_prompt(user_query)
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": prompt}],
                temperature=0.1,
                max_tokens=300
            )
            return self._parse_response(response.choices[0].message.content)
            
        except Exception as e:
            return {"error": str(e)}

    def _build_prompt(self, user_query):
        return f"""You are a MongoDB expert. Convert this natural language query to a valid MongoDB JSON query.
        Database Schema:
        {json.dumps(self.schema, indent=2)}

        Rules:
        1. Use only supported MongoDB operators
        2. Always specify collection name
        3. Never include comments
        4. For text searches, use $regex with case-insensitive option
        5. Use proper data types (e.g., numbers without quotes)
        6. If the user asks for the "count" of records, return:
        {{"collection": "<collection_name>", "filter": <filter_object>, "operation": "count"}}
        7. If the query requires data from multiple collections, use the $lookup aggregation stage to join the collections.

        Examples:
        - "Find applicants from California interested in Computer Science" => 
        {{"collection": "applicants", "filter": {{"state": "California", "program": "Computer Science"}}}}

        - "Show first 10 applicants with 10th percentage above 90" => 
        {{"collection": "Mark_List", "filter": {{"10th.percentage": {{"$gt": 90}}}}, "limit": 10}}

        - "Details and mark list of applicant 1" =>
        {{"collection": "applicants", "pipeline": [
            {{"$match": {{"applicant_id": 1}}}},
            {{"$lookup": {{
            "from": "Mark_List",
            "localField": "applicant_id",
            "foreignField": "applicant_id",
            "as": "mark_list"
            }}}}
        ], "operation": "aggregate"}}

        Query: {user_query}
        Response:"""
    
    def _parse_response(self, response_text):
        try:
            # Extract JSON from markdown-like response
            json_str = re.search(r'\{.*\}', response_text, re.DOTALL).group()
            query = json.loads(json_str)
            
            # Validate required fields
            if 'collection' not in query:
                raise ValueError("Collection name not specified")
            
            if 'filter' not in query:
                query['filter'] = {}

            # Ensure 'operation' is set correctly (if count is required)
            if 'operation' not in query:
                query['operation'] = 'find'  # Default to find
                
            return query
            
        except Exception as e:
            return {"error": f"Query parsing failed: {str(e)}"}

# Initialize Query Generator
query_generator = QueryGenerator(CONFIG)

# Routes
@app.route('/')
def home():
    return render_template('query_window4.html')

@app.route('/query', methods=['POST'])
def handle_query():
    try:
        # Validate input
        data = request.get_json()
        if not data or 'query' not in data:
            return jsonify({"error": "Invalid request format"}), 400
            
        user_query = data['query']
        sanitize_input(user_query)

        # Generate MongoDB query
        generated_query = query_generator.generate_query(user_query)
        if 'error' in generated_query:
            return jsonify(generated_query), 400

        # Execute query
        operation = generated_query.get('operation', 'find')

        if operation == 'count':
            result = {"count": mongo_adapter.execute_query(
                collection_name=generated_query['collection'],
                query={'filter': generated_query.get('filter', {})},
                operation='count'
            )}
        elif operation == 'aggregate':
            result = mongo_adapter.execute_query(
                collection_name=generated_query['collection'],
                query={'pipeline': generated_query.get('pipeline', [])},
                operation='aggregate'
            )
        else:
            result = mongo_adapter.execute_query(
                collection_name=generated_query['collection'],
                query={
                    'filter': generated_query.get('filter', {}),
                    'projection': generated_query.get('projection', {'_id': 0}),
                    'limit': generated_query.get('limit', 0)
                },
                operation=operation
            )

        print("user_query", user_query)
        print("generated_mongoDB_query:", generated_query)
        print("Result", result)

        # Convert MongoDB objects to JSON and include the generated query in the response
        response = {
            "generated_query": generated_query,
            "result": json.loads(json_util.dumps(result))
        }

        return jsonify(response)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route('/fetch_all_collections', methods=['GET'])
def fetch_all_collections():
    try:
        collections = {
            "applicants": list(mongo_adapter.execute_query(
                "applicants", 
                {"filter": {}, "projection": {"_id": 0}}
            )),
            "Mark_List": list(mongo_adapter.execute_query(
                "Mark_List", 
                {"filter": {}, "projection": {"_id": 0}}
            )),
            "Annual_Income": list(mongo_adapter.execute_query(
                "Annual_Income", 
                {"filter": {}, "projection": {"_id": 0}}
            ))
        }
        return jsonify(collections)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)