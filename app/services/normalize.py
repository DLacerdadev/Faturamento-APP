from typing import List, Dict, Any

FIELD_MAPPINGS = {
    'customer_name': 'name',
    'full_name': 'name',
    'client_name': 'name',
    'customer_email': 'email',
    'email_address': 'email',
    'phone_number': 'phone',
    'telephone': 'phone',
    'company_name': 'company',
    'organization': 'company',
    'street_address': 'address',
    'zip_code': 'postal_code',
    'zip': 'postal_code',
    'postcode': 'postal_code',
    'type': 'customer_type',
    'category': 'customer_type',
    'revenue': 'total_revenue',
    'total_sales': 'total_revenue',
}

def normalize_customer_data(raw_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized_data = []
    
    for record in raw_data:
        normalized_record = {}
        
        for key, value in record.items():
            normalized_key = key.lower().strip().replace(' ', '_')
            
            if normalized_key in FIELD_MAPPINGS:
                normalized_key = FIELD_MAPPINGS[normalized_key]
            
            if value == '' or value is None:
                continue
                
            normalized_record[normalized_key] = value
        
        if 'name' in normalized_record or 'email' in normalized_record:
            if 'status' not in normalized_record:
                normalized_record['status'] = 'active'
            if 'total_revenue' not in normalized_record:
                normalized_record['total_revenue'] = 0.0
            
            normalized_data.append(normalized_record)
    
    return normalized_data

def normalize_employee_data(raw_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    employee_mappings = {
        'firstname': 'first_name',
        'fname': 'first_name',
        'lastname': 'last_name',
        'lname': 'last_name',
        'dept': 'department',
        'job_title': 'position',
        'title': 'position',
        'wage': 'salary',
        'start_date': 'hire_date',
    }
    
    normalized_data = []
    
    for record in raw_data:
        normalized_record = {}
        
        for key, value in record.items():
            normalized_key = key.lower().strip().replace(' ', '_')
            
            if normalized_key in employee_mappings:
                normalized_key = employee_mappings[normalized_key]
            
            if value == '' or value is None:
                continue
                
            normalized_record[normalized_key] = value
        
        if 'first_name' in normalized_record and 'last_name' in normalized_record:
            if 'status' not in normalized_record:
                normalized_record['status'] = 'active'
            
            normalized_data.append(normalized_record)
    
    return normalized_data

def apply_custom_mapping(raw_data: List[Dict[str, Any]], mapping: Dict[str, str]) -> List[Dict[str, Any]]:
    normalized_data = []
    
    for record in raw_data:
        normalized_record = {}
        
        for source_field, target_field in mapping.items():
            if source_field in record:
                normalized_record[target_field] = record[source_field]
        
        normalized_data.append(normalized_record)
    
    return normalized_data
