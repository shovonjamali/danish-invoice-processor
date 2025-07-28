import logging
import os
import json
from typing import Dict, Any, Tuple, Optional, List
from datetime import datetime, timedelta
import openai
import time
import re
from config.settings import INVOICE_TEMPLATE_PATH
from config.settings import USE_DEFAULT_CUSTOMER_ONLY

import uuid
from utils.token_tracker import update_token_usage, get_token_usage, get_cost_estimate, reset_counters

logger = logging.getLogger(__name__)

class InvoiceService:
    """Service for generating invoice files"""
    
    def __init__(self):
        """Initialize Invoice Service"""
        logger.info("Initializing Invoice Service")
        # Make sure API key is loaded from environment
        self.api_key = os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            logger.warning("OPENAI_API_KEY not found in environment variables")
        else:
            openai.api_key = self.api_key
            
        # Define constants for chunking
        self.CHUNK_SIZE = 3000  # approximate tokens
        self.CHUNK_OVERLAP = 500  # overlap between chunks to maintain context

        # Initialize token tracking
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0

    def format_amount(self, amount):
        try:
            # Convert to float and round to 2 decimal places
            amount_float = float(amount)
            amount_rounded = round(amount_float, 2)
            
            # Format with 2 decimal places
            return f"{amount_rounded:.2f}"
        except:
            return "0.00"    
    
    def generate_invoice(self, markdown_content: str) -> Tuple[Optional[str], Optional[Dict[str, Any]], Dict[str, int]]:
        try:
            if not markdown_content:
                logger.error("No markdown content provided")
                return None, None, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                    
            logger.info("Generating invoice from markdown content")
                
            # 1. Split the markdown into manageable chunks
            chunks = self._split_content_into_chunks(markdown_content)
            logger.info(f"Split markdown into {len(chunks)} chunks")
                
            # 2. Extract invoice data from each chunk
            invoice_data = self._extract_invoice_data_from_chunks(chunks)
            if not invoice_data:
                logger.error("Failed to extract invoice data from chunks")
                return None, None, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            
            # 3. Enrich with CVR numbers looked up from company names
            logger.info("Enriching invoice data with CVR numbers from company names")
            invoice_data = self.enrich_with_cvr_numbers(invoice_data)
                    
            # 4. Get OIOXML template
            template = self._load_invoice_template()
            if not template:
                logger.error("Failed to load invoice template")
                return None, None, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                
            # 5. Generate OIOXML using the template and extracted data
            xml_content = self._generate_xml_from_data(template, invoice_data)
                
            if not xml_content:
                logger.error("Failed to generate XML from data")
                return None, None, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                
            # 6. Save XML to file
            output_dir = os.path.join(os.getcwd(), "output")
            os.makedirs(output_dir, exist_ok=True)
                
            # Create a unique filename based on timestamp
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            invoice_number = invoice_data.get("invoice_number", "unknown")
            safe_invoice_number = re.sub(r'[^\w\-]', '_', invoice_number)
                
            invoice_file_path = os.path.join(output_dir, f"invoice_{safe_invoice_number}_{timestamp}.xml")
                
            with open(invoice_file_path, 'w', encoding='utf-8') as f:
                f.write(xml_content)
                    
            logger.info(f"Invoice generated and saved to: {invoice_file_path}")
                
            logger.info(f"Current token usage after invoice generation: {self.get_token_usage_summary()}")

            # Return token usage along with invoice data
            token_usage = {
                "prompt_tokens": getattr(self, "total_prompt_tokens", 0),
                "completion_tokens": getattr(self, "total_completion_tokens", 0),
                "total_tokens": getattr(self, "total_tokens", 0)
            }
                
            return invoice_file_path, invoice_data, token_usage
                
        except Exception as e:
            logger.error(f"Error generating invoice: {e}")
            # Return empty token usage in case of error
            token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            return None, None, token_usage
    
    def _split_content_into_chunks(self, content: str) -> List[str]:
        # Split by paragraphs
        paragraphs = re.split(r'\n\s*\n', content)
        
        chunks = []
        current_chunk = []
        current_length = 0
        
        for paragraph in paragraphs:
            # Rough estimate of tokens (chars / 4)
            paragraph_length = len(paragraph) / 4
            
            if current_length + paragraph_length > self.CHUNK_SIZE and current_chunk:
                # Add the current chunk to our list and start a new one
                chunks.append('\n\n'.join(current_chunk))
                
                # Start a new chunk with some overlap (keep last few paragraphs)
                overlap_size = 0
                overlap_paragraphs = []
                
                for p in reversed(current_chunk):
                    overlap_size += len(p) / 4
                    overlap_paragraphs.insert(0, p)
                    if overlap_size > self.CHUNK_OVERLAP:
                        break
                
                current_chunk = overlap_paragraphs
                current_length = overlap_size
            
            current_chunk.append(paragraph)
            current_length += paragraph_length
        
        # Add the last chunk if it's not empty
        if current_chunk:
            chunks.append('\n\n'.join(current_chunk))
        
        return chunks

    def _extract_invoice_data_from_chunks(self, chunks: List[str]) -> Optional[Dict[str, Any]]:
        all_data = {}
        line_items = []
        
        # Join all chunks for analysis
        full_content = "\n".join(chunks)
        self.current_content = full_content  # Store for possible use later
        lines = full_content.split('\n')
        
        # ADD DEBUGGING
        logger.info("=== DEBUGGING INVOICE NUMBER EXTRACTION ===")
        logger.info(f"Total lines: {len(lines)}")
        
        # Print first 20 lines to see the structure
        for i, line in enumerate(lines[:20]):
            logger.info(f"Line {i}: '{line.strip()}'")
        
        # FIRST: Check for invoice number in the header format "Faktura XXXXX"
        try:
            for i, line in enumerate(lines):
                line_stripped = line.strip()
                logger.debug(f"Checking line {i}: '{line_stripped}'")
                
                # Check multiple patterns
                if "Faktura" in line_stripped:
                    logger.info(f"Found 'Faktura' in line {i}: '{line_stripped}'")
                    
                    # Pattern 1: "Faktura 112262" on same line
                    if line_stripped.startswith("Faktura") and len(line_stripped.split()) > 1:
                        parts = line_stripped.split()
                        potential_invoice_num = parts[1]
                        logger.info(f"Pattern 1 - Found potential invoice: {potential_invoice_num}")
                        if potential_invoice_num.replace('-', '').replace('.', '').isdigit():
                            all_data["invoice_number"] = potential_invoice_num
                            logger.info(f"Set invoice number: {potential_invoice_num}")
                            break
                    
                    # Pattern 2: "Faktura" on one line, number within next few lines (skipping empty)
                    if line_stripped == "Faktura":
                        # Check next 5 lines for a number
                        for j in range(1, 6):
                            if i + j < len(lines):
                                next_line = lines[i + j].strip()
                                logger.info(f"Checking line {i + j} after 'Faktura': '{next_line}'")
                                # Check if it's a number (could be invoice number)
                                if next_line and next_line.replace('-', '').replace('.', '').isdigit():
                                    all_data["invoice_number"] = next_line
                                    logger.info(f"Set invoice number from line {i + j}: {next_line}")
                                    break
                        if "invoice_number" in all_data:
                            break
                
        except Exception as e:
            logger.error(f"Error in header extraction: {e}")
        
        logger.info(f"Invoice number after header extraction: {all_data.get('invoice_number', 'NOT FOUND')}")

        try:
            # Find positions of key labels
            label_positions = {}
            for i, line in enumerate(lines):
                line = line.strip()
                if line == "Faktura":
                    label_positions["faktura"] = i
                elif line == "Fakturadato":
                    label_positions["fakturadato"] = i
                elif line == "Fakturakonto":
                    label_positions["fakturakonto"] = i
                elif line == "Nummer":
                    label_positions["nummer"] = i
            
            logger.info(f"Found label positions: {label_positions}")
            
            # If we found labels, look for values
            if label_positions:
                # Find the first value line (a line with digits after labels)
                first_value_line = -1
                for i in range(max(label_positions.values()) + 1, len(lines)):
                    if re.search(r'\d', lines[i].strip()):
                        first_value_line = i
                        break
                
                if first_value_line > 0:
                    logger.info(f"First value line: {first_value_line}")
                    
                    # The values are in vertical order matching the order of labels
                    # Find the order of labels by position
                    ordered_labels = sorted(label_positions.items(), key=lambda x: x[1])
                    logger.info(f"Ordered labels: {ordered_labels}")
                    
                    # Map values directly by position
                    # The date is always the first value
                    # The invoice number is always the third value
                    if len(lines) > first_value_line + 2:
                        date_value = lines[first_value_line].strip()
                        account_value = lines[first_value_line + 1].strip()
                        
                        logger.info(f"Extracted values: date={date_value}, account={account_value}")
                        
                        # Set values directly
                        all_data["invoice_date"] = date_value
                        all_data["billing_account"] = account_value
                        
                        # IMPORTANT: Only set invoice number if not already found
                        if "invoice_number" not in all_data:
                            invoice_value = lines[first_value_line + 2].strip()
                            all_data["invoice_number"] = invoice_value
                            logger.info(f"Assigned invoice_number from vertical layout: {invoice_value}")
                        else:
                            logger.info(f"Keeping existing invoice_number: {all_data.get('invoice_number')}")
                    
        except Exception as e:
            logger.error(f"Error in direct extraction: {e}")

        # Extract environmental fee and additional charges using LLM
        try:
            additional_charges = self._extract_additional_charges_with_llm(full_content)
            if additional_charges:
                all_data.update(additional_charges)
                logger.info(f"Found additional charges: {additional_charges}")
        except Exception as e:
            logger.error(f"Failed to extract additional charges: {e}")

        try:
            payment_details = self._extract_payment_details_with_llm(full_content)
            if payment_details:
                # Merge payment details including the calculated due date
                all_data.update(payment_details)
                logger.info(f"Updated data with payment details (including due date): {payment_details}")
        except Exception as e:
            logger.error(f"Failed to extract payment details: {e}")
        
        # Continue with OpenAI extraction for other fields
        for i, chunk in enumerate(chunks):
            try:
                logger.info(f"Processing chunk {i+1}/{len(chunks)}")
                prompt = self._create_extraction_prompt(chunk)
                chunk_data = self._extract_data_with_openai(prompt)
                
                if not chunk_data:
                    logger.warning(f"Failed to extract data from chunk {i+1}")
                    continue
                
                # Preserve our directly extracted invoice number
                if "invoice_number" in all_data and "invoice_number" in chunk_data:
                    logger.info(f"Direct extraction: {all_data['invoice_number']}, OpenAI extraction: {chunk_data['invoice_number']}")
                    del chunk_data["invoice_number"]
                
                # Handle line items
                if "line_items" in chunk_data:

                    # Additional logging for line items to debug
                    logger.info(f"EXTRACTION: Found {len(chunk_data['line_items'])} line items in chunk {i+1}")
                    for idx, item in enumerate(chunk_data['line_items']):
                        logger.info(f"EXTRACTION: Line {idx+1} from chunk {i+1}: {item}")

                    line_items.extend(chunk_data.pop("line_items", []))
                
                # Merge data
                all_data.update(chunk_data)
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"Error processing chunk {i+1}: {e}")
        
        # Add line items
        if line_items:
            all_data["line_items"] = line_items
        
        if not all_data:
            return None
        
        # Final logging
        logger.info(f"=== FINAL EXTRACTED DATA ===")
        logger.info(f"Invoice number: {all_data.get('invoice_number', 'NOT FOUND')}")
        logger.info(f"Invoice date: {all_data.get('invoice_date', 'NOT FOUND')}")
        logger.info(f"Billing account: {all_data.get('billing_account', 'NOT FOUND')}")
        
        return all_data


    def _extract_payment_details_with_llm(self, content: str) -> Dict[str, Any]:
        """
        Extract payment details from invoice text, handling both FIK and bank transfer payments
        """
        try:
            # Create an enhanced prompt that handles multiple payment types
            prompt = f"""
                Extract payment method information from this Danish invoice text.
                
                CRITICAL: Correctly identify the payment type:
                
                1. FIK PAYMENT (Code 93):
                - MUST have format: +71<...+...< or +73<...+...< or +75<...+...< 
                - Look for "Betalings-id:" or "+71<" patterns
                - Example: "+71<123456789012345+98765432<"
                - For FIK: payment_means_code = 93, payment_id = 71/73/75
                - instruction_id = 15 digits BEFORE the +
                - account_id = EXACTLY 8 digits AFTER the +
                
                2. BANK TRANSFER (Code 42):
                - NO FIK pattern present
                - Has IBAN, BIC/SWIFT, or regular bank account
                - Look for "Bank account:", "IBAN:", "BIC:", "SWIFT:"
                - For Bank Transfer: payment_means_code = 42
                
                3. UNSPECIFIED (Code 30):
                - No specific payment information
                - Only payment terms like "Netto 14 dage"
                
                IMPORTANT:
                - ONLY use code 93 if you find the FIK pattern (+71< etc.)
                - If no FIK pattern but bank details exist, use code 42
                - Double-check: FIK account_id MUST be exactly 8 digits
                
                Return JSON with:
                - payment_method_type: "FIK", "BANK_TRANSFER", or "UNSPECIFIED"
                - payment_means_code: 93 (FIK), 42 (bank), or 30 (unspecified)
                
                For FIK payments:
                - instruction_id: 15-digit instruction ID
                - payment_id: 71, 73, or 75 ONLY
                - account_id: EXACTLY 8 digits (pad with zeros if needed)
                
                For Bank transfers:
                - bank_account: Complete bank account
                - reg_number: 4-digit registration
                - account_number: Account number
                - iban: IBAN if present
                - bic: BIC/SWIFT if present
                
                Common:
                - payment_terms: Payment terms text
                - payment_due_date: Due date in YYYY-MM-DD
                
                RETURN ONLY JSON.

                Text:
                {content}
            """
            
            # Call OpenAI API
            response = openai.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are an expert in Danish invoice payment processing. Identify payment types and extract exact payment details. Return ONLY valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=500
            )

            update_token_usage(
                response.usage.prompt_tokens,
                response.usage.completion_tokens
            )
            
            # Get and clean the response
            content = response.choices[0].message.content.strip()
            
            # Debug logging
            logger.debug(f"Raw payment details response: {content}")
            
            # Clean up the response (remove markdown, extract JSON)
            if content.startswith("```json"):
                content = content.replace("```json", "", 1).strip()
            elif content.startswith("```"):
                content = content.replace("```", "", 1).strip()
                
            # Find the end of JSON object
            if content.startswith('{'):
                brace_count = 0
                json_end = -1
                
                for i, char in enumerate(content):
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            json_end = i + 1
                            break
                
                if json_end > 0:
                    content = content[:json_end]
            
            if content.endswith("```"):
                content = content[:-3].strip()
            
            # Parse the JSON response
            try:
                payment_details = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse payment details JSON: {e}")
                logger.error(f"Content: {content}")
                # Return default payment details
                return {
                    "payment_method_type": "UNSPECIFIED",
                    "payment_means_code": "30",
                    "payment_due_date": (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
                }
            
            # Validate based on payment type
            payment_type = payment_details.get("payment_method_type", "").upper()
            
            if payment_type == "FIK":
                # Validate FIK payment details
                if payment_details.get("payment_id") in ["71", "73", "75"]:
                    instruction_id = str(payment_details.get("instruction_id", "")).replace(" ", "")
                    if len(instruction_id) == 15:
                        payment_details["instruction_id"] = instruction_id
                    else:
                        logger.warning(f"Invalid instruction_id length: {len(instruction_id)}")
                        
                    account_id = str(payment_details.get("account_id", "")).replace(" ", "")
                    if len(account_id) == 8:
                        payment_details["account_id"] = account_id
                    else:
                        logger.warning(f"Invalid account_id length: {len(account_id)}")
                        
                    payment_details["payment_means_code"] = "93"
                    
            elif payment_type == "BANK_TRANSFER":
                # Ensure bank transfer has correct code
                payment_details["payment_means_code"] = "42"
                
                # Clean up IBAN if present
                if "iban" in payment_details:
                    iban = payment_details["iban"].replace(" ", "").upper()
                    payment_details["iban"] = iban
                    
            else:
                # Default to credit transfer if unspecified
                payment_details["payment_means_code"] = payment_details.get("payment_means_code", "30")
            
            logger.info(f"Extracted payment details: {payment_details}")
            
            return payment_details
            
        except Exception as e:
            logger.error(f"Error extracting payment details with LLM: {e}", exc_info=True)
            # Fallback to default payment method
            return {
                "payment_method_type": "UNSPECIFIED",
                "payment_means_code": "30",
                "payment_due_date": (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
            }

    def _extract_additional_charges_with_llm(self, content: str) -> Dict[str, Any]:
        """Extract environmental fees and other additional charges using LLM"""
        try:
            prompt = f"""
                Extract any additional charges or fees from this Danish invoice text.
                
                CRITICAL: Look for any environmental fees, shipping charges, or other additional costs that are NOT regular line items.
                
                Return a JSON object with these fields:

                - environmental_fee: Environmental fee amount (look for "Miljøafgift", "Miljøgebyr", "Environmental fee")
                - environmental_fee_description: The exact text describing the environmental fee
                - shipping_fee: Shipping or freight charges (look for "Fragt", "Transport", "Shipping")
                - shipping_fee_description: The exact text describing the shipping fee
                - other_charges: Array of other charges, each with:
                - description: Description of the charge
                - amount: The amount
                - subtotal_before_charges: The merchandise subtotal before any additional charges
                - subtotal_with_charges: The total after adding charges but before VAT
                
                IMPORTANT INSTRUCTIONS:
                1. Environmental fees are often listed separately AFTER the line items
                2. Shipping/freight charges are also typically shown after line items
                3. These fees are typically added to the merchandise subtotal BEFORE VAT calculation
                4. Look for amounts that appear after the main line items but before the final totals
                5. Extract the EXACT amounts shown in the invoice
                
                Example patterns to look for:
                - "Miljøafgift 190,64"
                - "Fragt 141,00"
                - "Fragt/transport 479,79"
                - Any charge that's not a regular product line item
                
                Text:
                {content}
            """
            
            # Call OpenAI API
            response = openai.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are an expert in Danish invoice processing. You must accurately identify and extract all additional charges and fees."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=500
            )

            update_token_usage(
                response.usage.prompt_tokens,
                response.usage.completion_tokens
            )
            
            # Get the response content
            content = response.choices[0].message.content.strip()
            
            # Debug logging
            logger.debug(f"Raw response from LLM: {content}")
            
            # If content is empty, return empty dict
            if not content:
                logger.warning("Empty response from LLM for additional charges")
                return {}
            
            # Clean up the response if it contains markdown formatting
            if content.startswith("```json"):
                content = content.replace("```json", "", 1).strip()
            elif content.startswith("```"):
                content = content.replace("```", "", 1).strip()
                
            if content.endswith("```"):
                content = content[:-3].strip()
            
            # Try to parse the JSON response
            try:
                charges_data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response: {e}")
                logger.error(f"Response content: {content}")
                # Return empty dict if parsing fails
                return {}
            
            logger.info(f"Extracted additional charges: {charges_data}")
            
            return charges_data
            
        except Exception as e:
            logger.error(f"Error extracting additional charges with LLM: {e}", exc_info=True)
            return {}

    def _create_extraction_prompt(self, chunk: str) -> str:
        return f"""
            Extract all possible invoice information from this text. Return the data as a JSON object with these fields if present:

            - invoice_number: The invoice number ONLY (look for 'FAKTURA NUMMER', 'Fakturanummer' - this should be just a number like '3341219')
            - billing_account: The customer account number (look for 'Fakturakonto', 'Kontonummer', or similar)
            - invoice_date: The date the invoice was issued (YYYY-MM-DD format)
            - due_date: The payment due date (YYYY-MM-DD format)
            - currency: The currency code (e.g., DKK, EUR, USD)
            
            # IMPORTANT: Look for these specific reference fields
            - customer_reference: The customer's reference (look for 'DERES REF.', 'Deres ref', 'Deres reference')
            - order_number: ONLY the case/order number (look for 'SAGS. NR.', which is usually followed by a shorter number like '4028204'). DO NOT include any other numbers like customer number, zip codes, etc.

            # IMPORTANT: For Danish invoices, the customer information is at the TOP of the invoice
            - customer_name: The name of the customer (typically at the TOP of the invoice)
            - customer_cvr: The CVR number of the customer (look for 'CVR', 'CVR nr.', 'CVR-nr', or an 8-digit number)
            - customer_vat: The VAT number of the customer (look for 'SE', 'SE nr.', 'Moms nr.', or 'DK' followed by 8 digits)
            - customer_street: The street address of the customer
            - customer_city: The city of the customer
            - customer_postal_code: The postal/zip code of the customer
            - customer_country: The country of the customer (use 2-letter code like DK, SE)

            # IMPORTANT: The supplier information is usually at the BOTTOM of the invoice and the header
            - supplier_name: The name of the supplier/vendor (often found at the BOTTOM of the invoice or in the header)
            - supplier_cvr: The CVR number of the supplier (look for 'CVR nr.:', 'CVR-nr:', 'CVR:' followed by 8 digits)
            - supplier_vat: The VAT/SE number of the supplier (look for 'SE nr.:', 'SE-nr:', 'Moms nr.:' followed by 'DK' and 8 digits)
            - supplier_street: The street address of the supplier
            - supplier_city: The city of the supplier
            - supplier_postal_code: The postal/zip code of the supplier
            - supplier_country: The country of the supplier (use 2-letter code like DK, SE)

            # CRITICAL: DO NOT confuse order number with other numbers in the document!
            - The order number is specifically labeled as 'SAGS. NR.' and is usually a shorter number (4-8 digits)
            - DO NOT combine different numbers from the document
            - If you cannot clearly identify the order number, leave it blank

            # IMPORTANT: Danish companies have TWO identification numbers:
            # 1. CVR number (Central Business Register): Always 8 digits (e.g., 55828415)
            # 2. VAT number (SE number): Usually 'DK' followed by 8 digits (e.g., DK12683693)
            # Make sure to extract BOTH if present, and put them in the correct fields!

            - subtotal: The subtotal amount (before tax and any additional charges)
            - tax_amount: The total tax/VAT amount
            - tax_percent: The tax/VAT percentage (e.g., 25 for 25%)
            - total_amount: The total amount (including tax)
            - payment_terms: The payment terms (e.g., "30 days", "Netto 14 dage")
            - payment_means_code: The payment means code (e.g., 30 for credit transfer, 42 for bank account)
            
            # CRITICAL - SPECIAL PARSING INSTRUCTIONS FOR LINE ITEMS
            - line_items: An array of items with these fields for each:
                - item_number: The product or item number/code (if available)
                - description: The description of the item
                - quantity: The quantity as a NUMBER ONLY (e.g., 5, not "5 stk", not "m5")
                - unit: The unit of measure as TEXT ONLY (e.g., "stk", "m", "kg", NOT "5 stk", NOT "m5")
                - unit_price: The price per unit
                - discount: The discount percentage (if present in the document, e.g., "62.00" would be a 62% discount)
                - amount: The total amount for this line (after discount if applicable)
                
            # CRITICAL - SPECIAL HANDLING FOR MERGED QUANTITY AND UNIT:
            - If you see formats like "m5", "stk3", "kg10" in the unit column, ALWAYS split these into:
            - unit: Just the letters (e.g., "m", "stk", "kg")
            - quantity: Just the numbers (e.g., 5, 3, 10)
            - For example, if you see "m5" in the unit column, extract:
            - unit: "m"
            - quantity: 5
            
            # CRITICAL - DISCOUNT HANDLING:
            - Look for percentage values like "62.00" that appear in the line item section
            - These typically represent discount percentages
            - Include these as "discount" in the line item if present

            Only include fields that you can identify from the text. Do not make up or assume information.
            Respond with ONLY the JSON object, nothing else.

            Text:
            {chunk}
        """
    
    def get_token_usage_summary(self) -> Dict[str, int]:
        return {
            "prompt_tokens": getattr(self, "total_prompt_tokens", 0),
            "completion_tokens": getattr(self, "total_completion_tokens", 0),
            "total_tokens": getattr(self, "total_tokens", 0)
        }

    def lookup_cvr_with_company_mapping(self, company_name: str) -> Optional[str]:
        import json
        import os
        
        # Path to the configuration file
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "company_cvr_map.json")
        
        # Load the mapping from the configuration file
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    company_cvr_map = config.get("company_cvr_map", {})
                    logger.info(f"Loaded {len(company_cvr_map)} company mappings from configuration")
            else:
                # Fallback to default mapping if config file doesn't exist
                logger.warning(f"Configuration file not found: {config_path}, using default mapping")
                company_cvr_map = {
                    "lego": "47458714",
                    "lego system": "47458714",
                    "universal robots": "29138060", 
                    "danfoss": "20165715",
                    "novo nordisk": "24256790",
                    "carlsberg": "25508343",
                    "carlsberg breweries": "25508343"
                }

                # Add a new mapping for GLN numbers
                gln_map = {
                    "lego": "5790000123456",
                    "lego system": "5790000123456",
                    "universal robots": "5790000234567",
                    "danfoss": "5790000345678", 
                    "novo nordisk": "5790000456789",
                    "carlsberg": "5790000567890",
                    "carlsberg breweries": "5790000567890"
                }
                
                # Check for GLN
                normalized_name = company_name.lower()
                for key, gln in gln_map.items():
                    if key in normalized_name:
                        return gln
        except Exception as e:
            logger.error(f"Error loading company mapping configuration: {e}")
            # Fallback to default mapping on error
            company_cvr_map = {
                "lego": "47458714",
                "lego system": "47458714",
                "universal robots": "29138060", 
                "danfoss": "20165715",
                "novo nordisk": "24256790",
                "carlsberg": "25508343",
                "carlsberg breweries": "25508343"
            }
        
        # Normalize the company name (lowercase and remove special chars)
        normalized_name = company_name.lower()
        
        # Check if any key in our mapping is a substring of the normalized name
        for key, cvr in company_cvr_map.items():
            if key in normalized_name:
                logger.info(f"Found CVR {cvr} for {company_name} in mapping")
                return cvr
        
        # If we get here, no match was found
        logger.warning(f"No CVR number found in mapping for: {company_name}")
        return None
    
    def enrich_with_cvr_numbers(self, data: Dict[str, Any]) -> Dict[str, Any]:
        # Look up supplier CVR if needed
        if 'supplier_name' in data and ('supplier_vat' not in data or not data['supplier_vat']):
            supplier_name = data['supplier_name']
            logger.info(f"Looking up CVR number for supplier: {supplier_name}")
            
            # Try the lookup using company mapping
            supplier_cvr = self.lookup_cvr_with_company_mapping(supplier_name)
            
            if supplier_cvr:
                data['supplier_vat'] = supplier_cvr
                logger.info(f"Found CVR number for supplier: {supplier_cvr}")
        
        # Look up customer CVR if needed
        if 'customer_name' in data and ('customer_vat' not in data or not data['customer_vat']):
            customer_name = data['customer_name']
            logger.info(f"Looking up CVR number for customer: {customer_name}")
            
            # Try the lookup using company mapping
            customer_cvr = self.lookup_cvr_with_company_mapping(customer_name)
            
            if customer_cvr:
                data['customer_vat'] = customer_cvr
                logger.info(f"Found CVR number for customer: {customer_cvr}")
        
        return data   

    def _extract_data_with_openai(self, prompt: str) -> Optional[Dict[str, Any]]:
        try:
            if not self.api_key:
                logger.error("Cannot call OpenAI API: No API key provided")
                return None
            
            # Use GPT-3.5-turbo for higher rate limits and extraction only task
            response = openai.chat.completions.create(
                # model="gpt-3.5-turbo",  # Use 3.5 for higher limits
                model = "gpt-4o",
                messages=[
                    {"role": "system", "content": "You are an expert in extracting structured data from invoice text. Always return valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,  # Low temperature for deterministic output
                max_tokens=1000
            )

            # Track token usage globally
            update_token_usage(
                response.usage.prompt_tokens,
                response.usage.completion_tokens
            )
            
            # Extract and parse the JSON response
            content = response.choices[0].message.content.strip()
            
            # Handle case where model might add code blocks
            if content.startswith("```json"):
                content = content.replace("```json", "", 1)
            elif content.startswith("```"):
                content = content.replace("```", "", 1)
                
            if content.endswith("```"):
                content = content[:-3]
                
            content = content.strip()
            
            # Attempt to fix common JSON issues
            try:
                # First try to parse as is
                extracted_data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"Initial JSON parsing failed: {e}")
                
                # Try to fix common issues with JSON
                # 1. Look for unterminated strings
                fixed_content = self._attempt_json_repair(content)
                
                # Try parsing the fixed content
                try:
                    extracted_data = json.loads(fixed_content)
                    logger.info("Successfully repaired and parsed JSON")
                except json.JSONDecodeError as e2:
                    logger.error(f"Could not repair JSON: {e2}")
                    # As a last resort, try a more lenient JSON parser or fall back to a default
                    return self._fallback_extraction()
            
            return extracted_data
            
        except Exception as e:
            logger.error(f"Error extracting data with OpenAI: {e}", exc_info=True)
            return None
            
    def _attempt_json_repair(self, content: str) -> str:
        # Replace escaped quotes that might be causing issues
        content = content.replace('\\"', '"')
        
        # Check for unescaped quotes in strings
        in_string = False
        fixed_content = ""
        i = 0
        
        while i < len(content):
            char = content[i]
            
            if char == '"' and (i == 0 or content[i-1] != '\\'):
                in_string = not in_string
            
            # If we're in a string and find a newline, replace it
            if in_string and char in ['\n', '\r']:
                fixed_content += '\\n'  # Properly escape the newline
            else:
                fixed_content += char
                
            i += 1
        
        # If we ended while still in a string, add closing quote
        if in_string:
            fixed_content += '"'
        
        # Try to balance braces and brackets
        braces = fixed_content.count('{') - fixed_content.count('}')
        brackets = fixed_content.count('[') - fixed_content.count(']')
        
        # Add any missing closing braces or brackets
        fixed_content += '}' * max(0, braces)
        fixed_content += ']' * max(0, brackets)
        
        return fixed_content
        
    def _fallback_extraction(self) -> Dict[str, Any]:
        logger.warning("Using fallback extraction due to JSON parsing failure")
        
        # Return a minimal valid structure with empty line items
        return {
            "invoice_number": "unknown",
            "invoice_date": datetime.now().strftime("%Y-%m-%d"),
            "currency": "DKK",
            "line_items": []
        }
    
    def _load_invoice_template(self) -> str:
        try:
            with open(INVOICE_TEMPLATE_PATH, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error loading invoice template: {e}")
            return ""

    def _prepare_invoice_data(self, data: Dict[str, Any], line_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Prepare invoice data with proper calculations for environmental fees
        FIXED: Ensure FIK payment data is properly formatted
        """

        # 1. Generate unique identifiers if missing
        if "invoice_number" not in data or not data["invoice_number"]:
            data["invoice_number"] = f"INV-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            
        if "uuid" not in data:
            data["uuid"] = str(uuid.uuid4())
            
        # 2. Set dates if missing
        if "invoice_date" not in data:
            data["invoice_date"] = datetime.now().strftime("%Y-%m-%d")
            
        # Use the payment due date extracted by LLM if available
        if "payment_due_date" not in data:
            try:
                invoice_date = datetime.strptime(data["invoice_date"], "%Y-%m-%d")
                days = data.get("days_to_payment", 30)
                due_date = invoice_date + timedelta(days=days)
                data["payment_due_date"] = due_date.strftime("%Y-%m-%d")
            except Exception as e:
                logger.error(f"Error calculating due date: {e}")
                data["payment_due_date"] = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
                
        # 3. Set currency if missing
        if "currency" not in data:
            data["currency"] = "DKK"
            
        # 4. Supply party information fallbacks
        # Supplier fallbacks
        if "supplier_name" not in data:
            data["supplier_name"] = "Unknown Supplier"
            
        if "supplier_country" not in data:
            data["supplier_country"] = "DK"
            
        if "supplier_vat" not in data:
            data["supplier_vat"] = "N/A"

        if not data.get("supplier_street"):
            data["supplier_street"] = "Unknown Street"
        if not data.get("supplier_city"):
            data["supplier_city"] = "Unknown City"
        if not data.get("supplier_postal_code"):
            data["supplier_postal_code"] = "0000"
            
        # Customer fallbacks
        if "customer_name" not in data:
            data["customer_name"] = "Unknown Customer"
            
        if "customer_country" not in data:
            data["customer_country"] = "DK"
            
        if "customer_vat" not in data:
            data["customer_vat"] = "N/A"
            
        # 5. Payment information fallbacks
        if "payment_means_code" not in data:
            # Determine default based on payment method type
            payment_type = data.get("payment_method_type", "").upper()
            if payment_type == "FIK":
                data["payment_means_code"] = "93"
            elif payment_type == "BANK_TRANSFER":
                data["payment_means_code"] = "42"
            else:
                data["payment_means_code"] = "30"  # Default to credit transfer
        else:
            # Validate and map the payment means code
            current_code = str(data["payment_means_code"])
            valid_codes = ["1", "10", "20", "31", "42", "48", "49", "50", "93", "97"]
            
            if current_code not in valid_codes:
                # Map common invalid codes to valid ones
                if current_code in ["71", "73", "75"]:  # FIK payment IDs
                    data["payment_means_code"] = "93"  # Use 93 for FIK payments
                else:
                    # Default based on payment type
                    payment_type = data.get("payment_method_type", "").upper()
                    if payment_type == "BANK_TRANSFER":
                        data["payment_means_code"] = "42"
                    else:
                        data["payment_means_code"] = "30"  # Default to credit transfer
                logger.info(f"Mapped invalid payment code {current_code} to {data['payment_means_code']}")
            
        # 6. Calculate monetary values
        if line_items:
            line_total = 0
            line_tax_total = 0
            
            for item in line_items:
                try:
                    qty = float(item.get("quantity", 0))
                    unit_price = float(item.get("unit_price", 0))
                    line_amount = qty * unit_price
                    
                    # Apply discount if present
                    discount = item.get("discount", 0)
                    if discount:
                        if isinstance(discount, str):
                            discount = float(discount.replace('%', '').strip())
                        else:
                            discount = float(discount)
                        line_amount = line_amount * (1 - discount/100)
                    
                    line_total += line_amount
                    
                    # Calculate tax on line
                    tax_percent = float(data.get("tax_percent", 25))
                    line_tax = round(line_amount * tax_percent / 100, 2)
                    line_tax_total += line_tax
                except:
                    continue
            
            # Round calculations properly
            line_total = round(line_total, 2)
            line_tax_total = round(line_tax_total, 2)
            
            # Set line extension amount (sum of lines only)
            data["line_extension_amount"] = line_total
            
            environmental_fee_raw = data.get("environmental_fee", 0)
            environmental_fee = 0
            
            try:
                if environmental_fee_raw:
                    # Handle string values
                    if isinstance(environmental_fee_raw, str):
                        # Remove common currency symbols and spaces
                        cleaned_value = environmental_fee_raw.replace('DKK', '').replace('kr', '').replace(',', '.').strip()
                        environmental_fee = float(cleaned_value)
                    else:
                        environmental_fee = float(environmental_fee_raw)
            except (ValueError, TypeError):
                logger.warning(f"Could not convert environmental_fee to float: {environmental_fee_raw}")
                environmental_fee = 0

            # ADD THIS SECTION for freight handling
            freight_fee_raw = data.get("shipping_fee", 0)
            freight_fee = 0

            try:
                if freight_fee_raw:
                    # Handle string values
                    if isinstance(freight_fee_raw, str):
                        # Remove common currency symbols and spaces
                        cleaned_value = freight_fee_raw.replace('DKK', '').replace('kr', '').replace(',', '.').strip()
                        freight_fee = float(cleaned_value)
                    else:
                        freight_fee = float(freight_fee_raw)
            except (ValueError, TypeError):
                logger.warning(f"Could not convert shipping_fee to float: {freight_fee_raw}")
                freight_fee = 0

            # Calculate total charges
            total_charges = environmental_fee + freight_fee
            
            tax_on_charges = 0
            if total_charges > 0:
                tax_on_charges = round(total_charges * tax_percent / 100, 2)
                data["charge_total_amount"] = total_charges
            
            # CRITICAL: TaxExclusiveAmount in OIOXML = total tax amount
            total_tax = line_tax_total + tax_on_charges

            data["tax_exclusive_amount"] = total_tax 
            
            # TaxInclusiveAmount = LineExtensionAmount + Tax + All Charges
            data["tax_inclusive_amount"] = line_total + total_tax + total_charges
            
            # Store the taxable amount for TaxSubtotal
            data["taxable_amount"] = line_total + total_charges

            # Store individual charge amounts for use in XML generation
            data["freight_fee"] = freight_fee
            
            # Also set these for consistency
            data["tax_amount"] = total_tax
            data["payable_amount"] = data["tax_inclusive_amount"]
            
        else:
            # No line items case
            data["line_extension_amount"] = 0
            data["tax_exclusive_amount"] = 0
            data["tax_amount"] = 0
            data["tax_inclusive_amount"] = 0
            data["payable_amount"] = 0
            data["taxable_amount"] = 0
        
        # 7. Add additional OIOXML metadata
        data["invoice_type_code"] = 380  # Standard invoice
        data["profile_id"] = "urn:www.nesubl.eu:profiles:profile5:ver2.0"
        data["schema_agency_id"] = 320
        data["schema_id"] = "urn:oioubl:id:profileid-1.2"
        data["line_count"] = len(line_items)
        
        # 8. Generate missing order references
        if "order_id" not in data:
            data["order_id"] = data.get("invoice_number", "UNKNOWN")
            
        if "sales_order_id" not in data:
            data["sales_order_id"] = data.get("invoice_number", "UNKNOWN")
            
        if "customer_reference" not in data:
            data["customer_reference"] = data.get("invoice_number", "")
        
        # 9. Add missing endpoint IDs (GLN)
        if "supplier_endpoint_id" not in data:
            # Use CVR as fallback if no GLN available
            data["supplier_endpoint_id"] = data.get("supplier_vat", "N/A")
            
        if "customer_endpoint_id" not in data:
            # Use CVR as fallback if no GLN available
            data["customer_endpoint_id"] = data.get("customer_vat", "N/A")

        
        return data

    def _generate_xml_from_data(self, template: str, data: Dict[str, Any]) -> str:
        try:
            logger.info("Generating XML from extracted data")
            
            # Extract line items for separate processing
            line_items = data.get("line_items", [])
            
            # Create a copy of data without line items to reduce prompt size
            base_data = {k: v for k, v in data.items() if k != "line_items"}
            
            # Enrich base data with defaults and calculated values
            base_data = self._prepare_invoice_data(base_data, line_items)
            
            # Use the enhanced OIOXML generation instead of template-based approach
            xml_content = self._generate_enhanced_oioxml(base_data, line_items)
            
            # If enhanced generation failed, fall back to the old template method
            if not xml_content:
                logger.warning("Enhanced OIOXML generation failed, falling back to template method")
                # The existing template-based generation code would go here
                # [...]
                
            return xml_content
            
        except Exception as e:
            logger.error(f"Error generating XML from data: {e}", exc_info=True)
            return ""

    def extract_order_reference_data(self, data):
        # Get customer reference and order number from LLM extraction
        customer_ref = data.get("customer_reference", "")
        order_number_from_llm = data.get("order_number", "")

        # Log what the LLM extracted
        logger.info(f"Raw LLM extraction - Customer Ref: '{customer_ref}', Order Number: '{order_number_from_llm}'")

        # Validate the order number - if it's suspiciously long, it might be wrong
        valid_order_number = False
        order_number = ""
        if order_number_from_llm:
            # Check if it's a reasonable length for an order number
            if len(order_number_from_llm) <= 8:
                order_number = order_number_from_llm
                valid_order_number = True
                logger.info(f"Using LLM-extracted order number: {order_number}")
            else:
                logger.warning(f"LLM-extracted order number too long ({len(order_number_from_llm)} digits), might be incorrect: {order_number_from_llm}")

        # If LLM extraction failed or produced invalid results, try direct pattern matching
        if not valid_order_number:
            # Look directly for SAGS. NR. in the text
            if hasattr(self, 'current_content'):
                content_lines = self.current_content.split('\n')
                
                # Log a sample of the content to see what we're working with
                logger.info(f"Searching for 'SAGS. NR.' in document with {len(content_lines)} lines")
                for i, line in enumerate(content_lines[:20]):  # Log first 20 lines for debugging
                    logger.debug(f"Line {i}: {line.strip()}")
                    
                # Search for SAGS. NR.
                for line in content_lines:
                    if "SAGS. NR" in line:
                        # Extract just the number after SAGS. NR.
                        import re
                        match = re.search(r'SAGS\.\s*NR.*?[:\.\s]+(\d+)', line)
                        if match:
                            order_number = match.group(1)
                            logger.info(f"Found SAGS. NR.: {order_number}")
                            break
                
                # If no match found, log the specific lines containing "SAGS"
                if not order_number:
                    logger.info("No match using regex, looking for lines containing 'SAGS':")
                    for line in content_lines:
                        if "SAGS" in line:
                            logger.info(f"Line with 'SAGS': {line.strip()}")

        # Check for alternate order number fields
        if not order_number:
            # Check for "KUNDE NR" as alternative
            if hasattr(self, 'current_content'):
                for line in self.current_content.split('\n'):
                    if "KUNDE NR" in line:
                        import re
                        match = re.search(r'KUNDE\s*NR.*?[:\.\s]+(\d+)', line)
                        if match:
                            order_number = match.group(1)
                            logger.info(f"Found KUNDE NR.: {order_number}")
                            break
            
            # Try looking for other fields in data
            if not order_number:
                for field in ["sags_nr", "ordrenr", "order_id"]:
                    if field in data and data[field] and len(str(data[field])) <= 8:
                        order_number = str(data[field])
                        logger.info(f"Using {field} as order number: {order_number}")
                        break
                
                # Last resort - look for the invoice number in a way that doesn't get other numbers
                if not order_number:
                    invoice_number = data.get("invoice_number", "")
                    if invoice_number and len(invoice_number) <= 8:
                        order_number = invoice_number
                        logger.info(f"Using invoice number as fallback: {order_number}")

        # Fix encoding issues in customer reference
        if customer_ref and "Fztex Zlgod" in customer_ref:
            customer_ref = "Føtex Ølgod"
            logger.info("Fixed encoding for Føtex Ølgod")

        # Extract order date (if needed)
        order_date = data.get("order_date", data.get("invoice_date", datetime.now().strftime("%Y-%m-%d")))
        
        return customer_ref, order_number, order_date
    
    def load_default_customer_config(self) -> Dict[str, str]:
        """Load default customer configuration from JSON file"""
        import json
        import os
        
        # Path to the configuration file
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "default_customer.json")
        
        # Default fallback values
        default_config = {
            "name": "Nordsjælland Teknik ApS",
            "vat": "DK29847156",
            "street": "Hovedgade 45B",
            "city": "Hillerød",
            "postal_code": "3400",
            "country": "DK",
            "contact_name": "Lars Nielsen",
            "contact_phone": "48262890"
        }
        
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    customer_config = config.get("default_customer", default_config)
                    logger.info(f"Loaded default customer configuration from {config_path}")
                    return customer_config
            else:
                logger.warning(f"Configuration file not found: {config_path}, using default values")
                return default_config
                
        except Exception as e:
            logger.error(f"Error loading default customer configuration: {e}")
            return default_config

    def _generate_enhanced_oioxml(self, data: Dict[str, Any], line_items: List[Dict[str, Any]]) -> str:
        """
        Generate OIOXML with proper structure
        FIXED: Correct TaxTotal to show document-level tax, not line-level
        """
        try:
            # Initialize line taxes collector
            self._line_taxes = []

            # Add storage for line extension amounts
            line_extension_amounts = []

            # Create XML string manually
            xml_parts = []
            
            # XML declaration
            xml_parts.append('<?xml version="1.0" encoding="UTF-8"?>')
            
            # Invoice root element with namespaces
            xml_parts.append('<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" ' +
                            'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" ' +
                            'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" ' +
                            'xmlns:ccts="urn:un:unece:uncefact:documentation:2" ' +
                            'xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2" ' +
                            'xmlns:qdt="urn:oasis:names:specification:ubl:schema:xsd:QualifiedDatatypes-2" ' +
                            'xmlns:udt="urn:un:unece:uncefact:data:specification:UnqualifiedDataTypesSchemaModule:2" ' +
                            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" ' +
                            'xsi:schemaLocation="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2 UBL-Invoice-2.0.xsd">')
            
            # Document information
            xml_parts.append('  <cbc:UBLVersionID>2.0</cbc:UBLVersionID>')
            xml_parts.append('  <cbc:CustomizationID>OIOUBL-2.02</cbc:CustomizationID>')
            xml_parts.append(f'  <cbc:ProfileID schemeAgencyID="{data.get("schema_agency_id", "320")}" ' +
                            f'schemeID="{data.get("schema_id", "urn:oioubl:id:profileid-1.2")}">' +
                            f'{data.get("profile_id", "urn:www.nesubl.eu:profiles:profile5:ver2.0")}</cbc:ProfileID>')
            xml_parts.append(f'  <cbc:ID>{data.get("invoice_number", "UNKNOWN")}</cbc:ID>')
            xml_parts.append('  <cbc:CopyIndicator>false</cbc:CopyIndicator>')
            xml_parts.append(f'  <cbc:UUID>{data.get("uuid", str(uuid.uuid4()))}</cbc:UUID>')
            xml_parts.append(f'  <cbc:IssueDate>{data.get("invoice_date", datetime.now().strftime("%Y-%m-%d"))}</cbc:IssueDate>')
            xml_parts.append(f'  <cbc:InvoiceTypeCode listAgencyID="320" listID="urn:oioubl:codelist:invoicetypecode-1.1">' +
                            f'{data.get("invoice_type_code", "380")}</cbc:InvoiceTypeCode>')
            
            # Add note if present
            if "note" in data:
                xml_parts.append(f'  <cbc:Note>{data.get("note", "")}</cbc:Note>')
                
            xml_parts.append(f'  <cbc:DocumentCurrencyCode>{data.get("currency", "DKK")}</cbc:DocumentCurrencyCode>')
            xml_parts.append(f'  <cbc:LineCountNumeric>{data.get("line_count", len(line_items))}</cbc:LineCountNumeric>')
            
            # Invoice period
            xml_parts.append('  <cac:InvoicePeriod>')
            xml_parts.append(f'    <cbc:StartDate>{data.get("invoice_date", datetime.now().strftime("%Y-%m-%d"))}</cbc:StartDate>')
            xml_parts.append('  </cac:InvoicePeriod>')

            # Order reference
            customer_ref, order_number, order_date = self.extract_order_reference_data(data)

            # Define order_id for reference elsewhere in the code
            order_id = order_number or data.get("invoice_number", "UNKNOWN")

            # Load default customer configuration
            default_customer = self.load_default_customer_config()

            # Generate OrderReference section
            xml_parts.append('  <cac:OrderReference>')
            xml_parts.append(f'    <cbc:ID>{customer_ref}</cbc:ID>')
            xml_parts.append(f'    <cbc:SalesOrderID>{order_number}</cbc:SalesOrderID>')
            xml_parts.append(f'    <cbc:IssueDate>{order_date}</cbc:IssueDate>')
            xml_parts.append('  </cac:OrderReference>')
            
            # Contract document reference (NEW)
            xml_parts.append('  <cac:ContractDocumentReference>')
            xml_parts.append(f'    <cbc:ID schemeID="CT">{data.get("contract_id", "1")}</cbc:ID>')
            xml_parts.append('  </cac:ContractDocumentReference>')
            
            # Accounting Supplier Party (Seller)
            xml_parts.append('  <cac:AccountingSupplierParty>')
            xml_parts.append('    <cac:Party>')

            # Use supplier_cvr for EndpointID with proper fallback
            supplier_cvr = data.get("supplier_cvr", "")
            if not supplier_cvr:
                # If no CVR directly, look for VAT and strip DK prefix
                supplier_vat = data.get("supplier_vat", "")
                if supplier_vat and supplier_vat.startswith("DK") and len(supplier_vat) >= 10:
                    supplier_cvr = supplier_vat[2:]
                else:
                    supplier_cvr = "00000000"  # Default if no CVR or VAT found
                    logger.warning("No supplier CVR found, using default")

            # Add the EndpointID with proper format
            xml_parts.append(f'      <cbc:EndpointID schemeID="DK:CVR">DK{supplier_cvr}</cbc:EndpointID>')
            
            # Clean the CVR (ensure 8 digits)
            supplier_cvr = ''.join(c for c in supplier_cvr if c.isdigit())
            if len(supplier_cvr) != 8:
                supplier_cvr = "00000000"  # Default if invalid format
                logger.warning(f"Invalid supplier CVR format, using default")


            # Fix: Only include GLN if we have a valid 13-digit GLN
            supplier_gln = data.get("supplier_gln", "")
            if supplier_gln and len(supplier_gln) == 13:
                xml_parts.append('      <cac:PartyIdentification>')
                xml_parts.append(f'        <cbc:ID schemeAgencyID="9" schemeID="GLN">{supplier_gln}</cbc:ID>')
                xml_parts.append('      </cac:PartyIdentification>')
            
            # Supplier name
            xml_parts.append('      <cac:PartyName>')
            xml_parts.append(f'        <cbc:Name>{data.get("supplier_name", "Unknown Supplier")}</cbc:Name>')
            xml_parts.append('      </cac:PartyName>')

            # Fix: Ensure address fields are never empty
            supplier_street = data.get("supplier_street", "Unknown Street")
            supplier_city = data.get("supplier_city", "Unknown City")
            supplier_postal = data.get("supplier_postal_code", "0000")
            
            # Supplier address
            xml_parts.append('      <cac:PostalAddress>')
            xml_parts.append('        <cbc:AddressFormatCode listAgencyID="320" listID="urn:oioubl:codelist:addressformatcode-1.1">StructuredDK</cbc:AddressFormatCode>')
            xml_parts.append(f'        <cbc:StreetName>{supplier_street}</cbc:StreetName>')
            xml_parts.append('        <cbc:BuildingNumber>.</cbc:BuildingNumber>')
            xml_parts.append(f'        <cbc:CityName>{supplier_city}</cbc:CityName>')
            xml_parts.append(f'        <cbc:PostalZone>{supplier_postal}</cbc:PostalZone>')
            xml_parts.append('        <cac:Country>')
            xml_parts.append(f'          <cbc:IdentificationCode>{data.get("supplier_country", "DK")}</cbc:IdentificationCode>')
            xml_parts.append('        </cac:Country>')
            xml_parts.append('      </cac:PostalAddress>')
            
            # Supplier tax scheme
            xml_parts.append('      <cac:PartyTaxScheme>')

            supplier_vat = data.get("supplier_vat", "")
            if not supplier_vat or len(supplier_vat) < 10:  # Not present or invalid format
                # Use CVR to create VAT if missing
                supplier_vat = f"DK{supplier_cvr}"
                logger.info(f"Created supplier VAT from CVR: {supplier_vat}")

            # Ensure proper format (DK + 8 digits)
            if not supplier_vat.startswith("DK"):
                supplier_vat = f"DK{supplier_vat}"
            supplier_vat = ''.join(c for c in supplier_vat if c.isalnum())  # Keep only alphanumeric
            if len(supplier_vat) != 10 or not supplier_vat.startswith("DK"):
                supplier_vat = f"DK{supplier_cvr}"  # Fallback to CVR-based VAT
                logger.warning(f"Invalid supplier VAT format, using CVR-based: {supplier_vat}")

            xml_parts.append(f'        <cbc:CompanyID schemeID="DK:SE">{supplier_vat}</cbc:CompanyID>')
            xml_parts.append('        <cac:TaxScheme>')
            xml_parts.append('          <cbc:ID schemeAgencyID="320" schemeID="urn:oioubl:id:taxschemeid-1.1">63</cbc:ID>')
            xml_parts.append('          <cbc:Name>Moms</cbc:Name>')
            xml_parts.append('        </cac:TaxScheme>')
            xml_parts.append('      </cac:PartyTaxScheme>')
            
            # Supplier legal entity
            xml_parts.append('      <cac:PartyLegalEntity>')
            xml_parts.append(f'        <cbc:RegistrationName>{data.get("supplier_name", "Unknown Supplier")}</cbc:RegistrationName>')
            xml_parts.append(f'        <cbc:CompanyID schemeID="DK:CVR">DK{supplier_cvr}</cbc:CompanyID>')
            xml_parts.append('      </cac:PartyLegalEntity>')
            
            # Supplier contact
            xml_parts.append('      <cac:Contact>')
            xml_parts.append('        <cbc:ID>n/a</cbc:ID>')
            xml_parts.append(f'        <cbc:Name>{data.get("supplier_contact", data.get("supplier_name", "Contact Person"))}</cbc:Name>')
            xml_parts.append('      </cac:Contact>')
            
            xml_parts.append('    </cac:Party>')
            xml_parts.append('  </cac:AccountingSupplierParty>')

            if USE_DEFAULT_CUSTOMER_ONLY:
                customer_vat = default_customer["vat"]
                customer_name = default_customer["name"]
                customer_street = default_customer["street"]
                customer_city = default_customer["city"]
                customer_postal = default_customer["postal_code"]
                customer_country = default_customer["country"]
                customer_contact = default_customer["contact_name"]
                customer_phone = default_customer["contact_phone"]
            else:
                customer_vat = data.get("customer_vat", default_customer["vat"])
                customer_name = data.get("customer_name", default_customer["name"])
                customer_street = data.get("customer_street", default_customer["street"])
                customer_city = data.get("customer_city", default_customer["city"])
                customer_postal = data.get("customer_postal_code", default_customer["postal_code"])
                customer_country = data.get("customer_country", default_customer["country"])
                customer_contact = data.get("customer_contact", default_customer["contact_name"])
                customer_phone = data.get("customer_phone", default_customer["contact_phone"])
            
            customer_name = customer_name.encode('utf-8').decode('utf-8', errors='replace')
            
            # Accounting Customer Party (Buyer)
            xml_parts.append('  <cac:AccountingCustomerParty>')
            xml_parts.append('    <cac:Party>')
            
            # Customer vat/CVR
            xml_parts.append(f'      <cbc:EndpointID schemeID="DK:CVR">{customer_vat}</cbc:EndpointID>')
            xml_parts.append('       <cac:PartyIdentification>')
            xml_parts.append(f'        <cbc:ID schemeID="DK:CVR">{customer_vat}</cbc:ID>')
            xml_parts.append('       </cac:PartyIdentification>')

            # Customer name
            xml_parts.append('      <cac:PartyName>')
            xml_parts.append(f'        <cbc:Name>{customer_name}</cbc:Name>')
            xml_parts.append('      </cac:PartyName>')
            
            # Customer address
            xml_parts.append('      <cac:PostalAddress>')
            xml_parts.append('        <cbc:AddressFormatCode listAgencyID="320" listID="urn:oioubl:codelist:addressformatcode-1.1">StructuredDK</cbc:AddressFormatCode>')
            xml_parts.append(f'        <cbc:StreetName>{customer_street}</cbc:StreetName>')
            xml_parts.append('        <cbc:BuildingNumber>.</cbc:BuildingNumber>')
            xml_parts.append(f'        <cbc:CityName>{customer_city}</cbc:CityName>')
            xml_parts.append(f'        <cbc:PostalZone>{customer_postal}</cbc:PostalZone>')
            xml_parts.append('        <cac:Country>')
            xml_parts.append(f'          <cbc:IdentificationCode>{customer_country}</cbc:IdentificationCode>')
            xml_parts.append('        </cac:Country>')
            xml_parts.append('      </cac:PostalAddress>')
            
            # Customer tax scheme - can be skipped, and skipping it
            # xml_parts.append('      <cac:PartyTaxScheme>')
            # xml_parts.append(f'        <cbc:CompanyID schemeID="DK:SE">{customer_vat}</cbc:CompanyID>')
            # xml_parts.append('        <cac:TaxScheme>')
            # xml_parts.append('          <cbc:ID schemeAgencyID="320" schemeID="urn:oioubl:id:taxschemeid-1.1">63</cbc:ID>')
            # xml_parts.append('          <cbc:Name>Moms</cbc:Name>')
            # xml_parts.append('        </cac:TaxScheme>')
            # xml_parts.append('      </cac:PartyTaxScheme>')
            
            # Customer legal entity
            xml_parts.append('      <cac:PartyLegalEntity>')
            xml_parts.append(f'        <cbc:RegistrationName>{customer_name}</cbc:RegistrationName>')
            xml_parts.append(f'        <cbc:CompanyID schemeID="DK:CVR">{customer_vat}</cbc:CompanyID>')
            xml_parts.append('      </cac:PartyLegalEntity>')
            
            # Customer contact
            xml_parts.append('      <cac:Contact>')
            xml_parts.append('        <cbc:ID>n/a</cbc:ID>')
            xml_parts.append(f'        <cbc:Name>{customer_contact}</cbc:Name>')
            xml_parts.append(f'        <cbc:Telephone>{customer_phone}</cbc:Telephone>')
            xml_parts.append('      </cac:Contact>')
            
            xml_parts.append('    </cac:Party>')
            xml_parts.append('  </cac:AccountingCustomerParty>')
            
            # Seller Supplier Party (copy of supplier info)
            xml_parts.append('  <cac:SellerSupplierParty>')
            xml_parts.append('    <cac:Party>')
            
            # Only include GLN if valid
            if supplier_gln and len(supplier_gln) == 13:
                xml_parts.append('      <cac:PartyIdentification>')
                xml_parts.append(f'        <cbc:ID schemeAgencyID="9" schemeID="GLN">{supplier_gln}</cbc:ID>')
                xml_parts.append('      </cac:PartyIdentification>')
            
            # Supplier name
            supplier_name = data.get("supplier_name", "Unknown Supplier")
            supplier_name = supplier_name.encode('utf-8').decode('utf-8', errors='replace')
            xml_parts.append('      <cac:PartyName>')
            xml_parts.append(f'        <cbc:Name>{supplier_name}</cbc:Name>')
            xml_parts.append('      </cac:PartyName>')
            
            # Use same address values as above
            xml_parts.append('      <cac:PostalAddress>')
            xml_parts.append('        <cbc:AddressFormatCode listAgencyID="320" listID="urn:oioubl:codelist:addressformatcode-1.1">StructuredDK</cbc:AddressFormatCode>')
            xml_parts.append(f'        <cbc:StreetName>{supplier_street}</cbc:StreetName>')
            xml_parts.append('        <cbc:BuildingNumber>.</cbc:BuildingNumber>')
            xml_parts.append(f'        <cbc:CityName>{supplier_city}</cbc:CityName>')
            xml_parts.append(f'        <cbc:PostalZone>{supplier_postal}</cbc:PostalZone>')
            xml_parts.append('        <cac:Country>')
            xml_parts.append(f'          <cbc:IdentificationCode>{data.get("supplier_country", "DK")}</cbc:IdentificationCode>')
            xml_parts.append('        </cac:Country>')
            xml_parts.append('      </cac:PostalAddress>')
            
            # Supplier tax scheme
            xml_parts.append('      <cac:PartyTaxScheme>')
            xml_parts.append(f'        <cbc:CompanyID schemeID="DK:SE">{supplier_vat}</cbc:CompanyID>')
            xml_parts.append('        <cac:TaxScheme>')
            xml_parts.append('          <cbc:ID schemeAgencyID="320" schemeID="urn:oioubl:id:taxschemeid-1.1">63</cbc:ID>')
            xml_parts.append('          <cbc:Name>Moms</cbc:Name>')
            xml_parts.append('        </cac:TaxScheme>')
            xml_parts.append('      </cac:PartyTaxScheme>')
            
            # Supplier legal entity
            xml_parts.append('      <cac:PartyLegalEntity>')
            xml_parts.append(f'        <cbc:RegistrationName>{data.get("supplier_name", "Unknown Supplier")}</cbc:RegistrationName>')
            xml_parts.append(f'        <cbc:CompanyID schemeID="DK:CVR">{supplier_vat}</cbc:CompanyID>')
            xml_parts.append('      </cac:PartyLegalEntity>')
            
            # Supplier contact
            xml_parts.append('      <cac:Contact>')
            xml_parts.append('        <cbc:ID>n/a</cbc:ID>')
            xml_parts.append(f'        <cbc:Name>{data.get("supplier_contact", data.get("supplier_name", "Contact Person"))}</cbc:Name>')
            xml_parts.append('      </cac:Contact>')
            
            xml_parts.append('    </cac:Party>')
            xml_parts.append('  </cac:SellerSupplierParty>')
            
            # Payment means
            xml_parts.append('  <cac:PaymentMeans>')
            xml_parts.append('    <cbc:ID>1</cbc:ID>')
            payment_means_code = str(data.get("payment_means_code", "42"))
            xml_parts.append(f'    <cbc:PaymentMeansCode>{payment_means_code}</cbc:PaymentMeansCode>')
            xml_parts.append(f'    <cbc:PaymentDueDate>{data.get("payment_due_date", "")}</cbc:PaymentDueDate>')

            # Add payment channel code based on payment type
            if payment_means_code == "93":
                # FIK payment
                xml_parts.append('    <cbc:PaymentChannelCode listAgencyID="320" listID="urn:oioubl:codelist:paymentchannelcode-1.1">DK:FIK</cbc:PaymentChannelCode>')
            elif payment_means_code == "42":
                # Bank transfer
                xml_parts.append('    <cbc:PaymentChannelCode listAgencyID="320" listID="urn:oioubl:codelist:paymentchannelcode-1.1">DK:BANK</cbc:PaymentChannelCode>')

            # Handle different payment types
            payment_method_type = data.get("payment_method_type", "").upper()

            if payment_method_type == "FIK" or payment_means_code == "93":
                # FIK payment
                
                # InstructionID (optional, but usually present for FIK)
                if "instruction_id" in data:
                    xml_parts.append(f'    <cbc:InstructionID>{data["instruction_id"]}</cbc:InstructionID>')
                
                # PaymentID (mandatory for FIK)
                payment_id = data.get("payment_id", "71")
                if payment_id not in ["71", "73", "75"]:
                    logger.warning(f"Invalid payment_id: {payment_id}, defaulting to 71")
                    payment_id = "71"
                xml_parts.append(f'    <cbc:PaymentID schemeAgencyID="320" schemeID="urn:oioubl:id:paymentid-1.1">{payment_id}</cbc:PaymentID>')
                
                # CreditAccount (aggregate element, comes last)
                account_id = str(data.get("account_id", "")).strip()
                if len(account_id) != 8:
                    logger.warning(f"FIK account_id must be 8 chars, got {len(account_id)}: {account_id}")
                    if len(account_id) < 8:
                        account_id = account_id.zfill(8)  # Pad left with zeros
                    else:
                        account_id = account_id[:8]  # Truncate to 8
                
                xml_parts.append('    <cac:CreditAccount>')
                xml_parts.append(f'      <cbc:AccountID>{account_id}</cbc:AccountID>')
                xml_parts.append('    </cac:CreditAccount>')

            elif payment_method_type == "BANK_TRANSFER" or payment_means_code == "42":
                # Bank transfer payment
                
                # PayeeFinancialAccount (aggregate element)
                xml_parts.append('    <cac:PayeeFinancialAccount>')
                
                # Extract account details
                account_number = ""
                reg_number = ""
                
                # Use the extracted values from LLM
                if "reg_number" in data and data["reg_number"]:
                    reg_number = str(data["reg_number"]).strip()
                
                if "account_number" in data and data["account_number"]:
                    account_number = str(data["account_number"]).strip()
                elif "bank_account" in data and data["bank_account"]:
                    # Fallback: if we have combined bank_account
                    bank_account = str(data["bank_account"])
                    bank_account_clean = bank_account.replace(" ", "").replace("-", "")
                    
                    if len(bank_account_clean) > 10 and not account_number:
                        if not reg_number:
                            reg_number = bank_account_clean[:4]
                        account_number = bank_account_clean[-10:]
                    else:
                        account_number = bank_account_clean
                
                # Validate
                if not reg_number:
                    reg_number = "0000"
                if not account_number:
                    account_number = "0000000000"
                
                reg_number = reg_number.zfill(4)
                
                xml_parts.append(f'      <cbc:ID>{account_number}</cbc:ID>')
                xml_parts.append(f'      <cbc:Name>{data.get("supplier_name", "")}</cbc:Name>')
                
                # FinancialInstitutionBranch
                xml_parts.append('      <cac:FinancialInstitutionBranch>')
                xml_parts.append(f'        <cbc:ID>{reg_number}</cbc:ID>')
                
                # Add BIC if available
                if "bic" in data and data["bic"]:
                    xml_parts.append('        <cac:FinancialInstitution>')
                    xml_parts.append(f'          <cbc:ID schemeID="BIC">{data["bic"]}</cbc:ID>')
                    xml_parts.append('        </cac:FinancialInstitution>')
                
                xml_parts.append('      </cac:FinancialInstitutionBranch>')
                xml_parts.append('    </cac:PayeeFinancialAccount>')

            else:
                # Default/unspecified payment
                pass

            xml_parts.append('  </cac:PaymentMeans>')
            # End of PaymentMeans
            
            # Payment terms
            xml_parts.append('  <cac:PaymentTerms>')
            xml_parts.append('    <cbc:ID>1</cbc:ID>')
            xml_parts.append('    <cbc:PaymentMeansID>1</cbc:PaymentMeansID>')
            xml_parts.append('    <cbc:SettlementDiscountPercent>0.00</cbc:SettlementDiscountPercent>')
            xml_parts.append(f'    <cbc:Amount currencyID="{data.get("currency", "DKK")}">{self.format_amount(data.get("payable_amount", "0"))}</cbc:Amount>')
            xml_parts.append('    <cac:SettlementPeriod>')
            xml_parts.append(f'      <cbc:EndDate>{data.get("payment_due_date", "")}</cbc:EndDate>')
            xml_parts.append('    </cac:SettlementPeriod>')
            xml_parts.append('  </cac:PaymentTerms>')

            # AllowanceCharge for environmental fee (if exists)
            environmental_fee = data.get("environmental_fee", 0)
            if environmental_fee and environmental_fee > 0:
                environmental_fee = float(environmental_fee)  # Ensure it's a float
                xml_parts.append('  <cac:AllowanceCharge>')
                xml_parts.append('    <cbc:ChargeIndicator>true</cbc:ChargeIndicator>')
                xml_parts.append('    <cbc:AllowanceChargeReasonCode>ENV</cbc:AllowanceChargeReasonCode>')
                xml_parts.append('    <cbc:AllowanceChargeReason>Miljøafgift</cbc:AllowanceChargeReason>')
                xml_parts.append(f'    <cbc:Amount currencyID="{data.get("currency", "DKK")}">{self.format_amount(environmental_fee)}</cbc:Amount>')
                xml_parts.append('    <cac:TaxCategory>')
                xml_parts.append('      <cbc:ID schemeAgencyID="320" schemeID="urn:oioubl:id:taxcategoryid-1.1">StandardRated</cbc:ID>')
                # xml_parts.append(f'      <cbc:Percent>{data.get("tax_percent", "25")}</cbc:Percent>')
                xml_parts.append('          <cbc:Percent>25.00</cbc:Percent>')
                xml_parts.append('      <cac:TaxScheme>')
                xml_parts.append('        <cbc:ID schemeAgencyID="320" schemeID="urn:oioubl:id:taxschemeid-1.1">63</cbc:ID>')
                xml_parts.append('        <cbc:Name>Moms</cbc:Name>')
                xml_parts.append('      </cac:TaxScheme>')
                xml_parts.append('    </cac:TaxCategory>')
                xml_parts.append('  </cac:AllowanceCharge>')

            # AllowanceCharge for freight fee (if exists)
            freight_fee = data.get("freight_fee", 0)
            if freight_fee and freight_fee > 0:
                freight_fee = float(freight_fee)  # Ensure it's a float
                xml_parts.append('  <cac:AllowanceCharge>')
                xml_parts.append('    <cbc:ChargeIndicator>true</cbc:ChargeIndicator>')
                xml_parts.append('    <cbc:AllowanceChargeReasonCode>FC</cbc:AllowanceChargeReasonCode>')  # FC = Freight Charge
                xml_parts.append('    <cbc:AllowanceChargeReason>Fragt</cbc:AllowanceChargeReason>')
                xml_parts.append(f'    <cbc:Amount currencyID="{data.get("currency", "DKK")}">{self.format_amount(freight_fee)}</cbc:Amount>')
                xml_parts.append('    <cac:TaxCategory>')
                xml_parts.append('      <cbc:ID schemeAgencyID="320" schemeID="urn:oioubl:id:taxcategoryid-1.1">StandardRated</cbc:ID>')
                # xml_parts.append(f'      <cbc:Percent>{data.get("tax_percent", "25")}</cbc:Percent>')
                xml_parts.append('          <cbc:Percent>25.00</cbc:Percent>')
                xml_parts.append('      <cac:TaxScheme>')
                xml_parts.append('        <cbc:ID schemeAgencyID="320" schemeID="urn:oioubl:id:taxschemeid-1.1">63</cbc:ID>')
                xml_parts.append('        <cbc:Name>Moms</cbc:Name>')
                xml_parts.append('      </cac:TaxScheme>')
                xml_parts.append('    </cac:TaxCategory>')
                xml_parts.append('  </cac:AllowanceCharge>')

            # CRITICAL FIX: Calculate the correct tax amounts
            # The real tax should be 25% of tax_exclusive_amount
            tax_exclusive = float(data.get("tax_exclusive_amount", 0))
            real_tax_amount = round(tax_exclusive * float(data.get("tax_percent", 25)) / 100, 2)
            
            # Tax total
            xml_parts.append('  <cac:TaxTotal>')
            
            # Get the total tax amount from data (which should already be calculated correctly)
            total_tax = float(data.get("tax_amount", 0))
            
            # Get the taxable amount (what the tax is calculated on)
            taxable_amount = float(data.get("taxable_amount", data.get("line_extension_amount", 0)))
            
            xml_parts.append(f'    <cbc:TaxAmount currencyID="{data.get("currency", "DKK")}">{self.format_amount(total_tax)}</cbc:TaxAmount>')
            xml_parts.append('    <cac:TaxSubtotal>')
            xml_parts.append(f'      <cbc:TaxableAmount currencyID="{data.get("currency", "DKK")}">{self.format_amount(taxable_amount)}</cbc:TaxableAmount>')
            xml_parts.append(f'      <cbc:TaxAmount currencyID="{data.get("currency", "DKK")}">{self.format_amount(total_tax)}</cbc:TaxAmount>')
            xml_parts.append('      <cac:TaxCategory>')
            xml_parts.append('        <cbc:ID schemeAgencyID="320" schemeID="urn:oioubl:id:taxcategoryid-1.1">StandardRated</cbc:ID>')
            # xml_parts.append(f'        <cbc:Percent>{data.get("tax_percent", "25.00")}</cbc:Percent>')
            xml_parts.append('          <cbc:Percent>25.00</cbc:Percent>')
            xml_parts.append('        <cac:TaxScheme>')
            xml_parts.append('          <cbc:ID schemeAgencyID="320" schemeID="urn:oioubl:id:taxschemeid-1.1">63</cbc:ID>')
            xml_parts.append('          <cbc:Name>Moms</cbc:Name>')
            xml_parts.append('        </cac:TaxScheme>')
            xml_parts.append('      </cac:TaxCategory>')
            xml_parts.append('    </cac:TaxSubtotal>')
            xml_parts.append('  </cac:TaxTotal>')

            # Log the calculation for debugging
            # logger.info(f"Validator workaround: TaxExclusive={tax_exclusive}, LinesTaxSum={line_taxes_sum}, AdjustedDocTax={document_tax_adjusted}")
            
            # Legal monetary total with precise amounts
            xml_parts.append('  <cac:LegalMonetaryTotal>')
            xml_parts.append(f'    <cbc:LineExtensionAmount currencyID="{data.get("currency", "DKK")}">{self.format_amount(data.get("line_extension_amount", "0"))}</cbc:LineExtensionAmount>')
            ## FIX: Use the total tax amount calculated above
            xml_parts.append(f'    <cbc:TaxExclusiveAmount currencyID="{data.get("currency", "DKK")}">{self.format_amount(total_tax)}</cbc:TaxExclusiveAmount>')
            xml_parts.append(f'    <cbc:TaxInclusiveAmount currencyID="{data.get("currency", "DKK")}">{self.format_amount(data.get("tax_inclusive_amount", "0"))}</cbc:TaxInclusiveAmount>')
            # Add ChargeTotalAmount when charges exist
            if data.get("charge_total_amount", 0) > 0:
                xml_parts.append(f'    <cbc:ChargeTotalAmount currencyID="{data.get("currency", "DKK")}">{self.format_amount(data.get("charge_total_amount", "0"))}</cbc:ChargeTotalAmount>')
            xml_parts.append(f'    <cbc:PayableAmount currencyID="{data.get("currency", "DKK")}">{self.format_amount(data.get("payable_amount", "0"))}</cbc:PayableAmount>')
            xml_parts.append('  </cac:LegalMonetaryTotal>')
            
            # Invoice lines
            for idx, item in enumerate(line_items, 1):
                xml_parts.append('  <cac:InvoiceLine>')
                xml_parts.append(f'    <cbc:ID>{idx}</cbc:ID>')

                # Extract quantity first
                quantity = item.get("quantity", "1.000")
                if isinstance(quantity, (int, float)):
                    quantity = f"{float(quantity):.3f}"
                else:
                    # Clean and format the quantity
                    quantity = str(quantity).replace(',', '.')
                    try:
                        quantity = f"{float(quantity):.3f}"
                    except:
                        quantity = "1.000"
                
                # Quantity with unit code
                unit = item.get("unit", "EA")  # Default to EA (Each)

                # Map common Danish units to valid UN/ECE codes
                unit_mapping = {
                    "stk": "EA",      # stykker -> Each
                    "stk.": "EA",     # stykker -> Each
                    "szet": "SET",    # sæt -> Set
                    "sæt": "SET",     # sæt -> Set
                    "pk": "PK",       # pakke -> Package
                    "pk.": "PK",      # pakke -> Package
                    "m": "MTR",       # meter -> Metre
                    "kg": "KGM",      # kilogram -> Kilogram
                    "l": "LTR",       # liter -> Litre
                    "timer": "HUR",   # timer -> Hour
                    "time": "HUR",    # time -> Hour
                    "dag": "DAY",     # dag -> Day
                    "dage": "DAY",    # dage -> Day
                    "kasse": "CS",    # kasse -> Case
                    "rulle": "RO",    # rulle -> Roll
                    "flaske": "BO",   # flaske -> Bottle
                    "palle": "PF",    # palle -> Pallet
                    "boks": "BX",     # boks -> Box
                }

                # Convert to lowercase for comparison and map
                unit_lower = unit.lower()

                if unit_lower in unit_mapping:
                    unit = unit_mapping[unit_lower]
                else:
                    # If no mapping found and it's not already uppercase, capitalize it
                    if unit != unit.upper():
                        # Check if it might be a known code in wrong case
                        if unit.upper() in ["EA", "SET", "PK", "KGM", "MTR", "LTR", "HUR", "DAY"]:
                            unit = unit.upper()
                        else:
                            # Default to EA for unknown units
                            logger.warning(f"Unknown unit '{unit}', defaulting to EA")
                            unit = "EA"

                xml_parts.append(f'    <cbc:InvoicedQuantity unitCode="{unit}">{quantity}</cbc:InvoicedQuantity>')
                
                # Calculate line extension amount
                try:
                    qty = float(quantity)
                    unit_price = float(item.get("unit_price", 0))
                    
                    # IMPORTANT: Check if there's a discount
                    discount = item.get("discount", 0)
                    discounted_unit_price = unit_price  # Default to original price
                    
                    if discount:
                        if isinstance(discount, str):
                            discount = float(discount.replace('%', '').strip())
                        else:
                            discount = float(discount)
                        # Calculate the discounted unit price
                        discounted_unit_price = unit_price * (1 - discount/100)
                    
                    # Store both prices for later use
                    item['original_unit_price'] = unit_price
                    item['discounted_unit_price'] = discounted_unit_price
                    
                    # Line amount is calculated with discounted price
                    line_amount_raw = round(qty * discounted_unit_price, 2)
                except:
                    line_amount_raw = float(item.get("amount", 0))

                 # Tax total for line
                tax_percent = float(data.get("tax_percent", 25))
                tax_amount_raw = round(line_amount_raw * tax_percent / 100, 2)
                
                line_amount = self.format_amount(line_amount_raw)
                tax_amount = self.format_amount(tax_amount_raw)

                # Store raw tax amount for later aggregation
                if not hasattr(self, '_line_taxes'):
                    self._line_taxes = []
                self._line_taxes.append(tax_amount_raw)

                # Store the line extension amount for later validation
                line_extension_amounts.append(line_amount_raw)
                
                xml_parts.append(f'    <cbc:LineExtensionAmount currencyID="{data.get("currency", "DKK")}">{line_amount}</cbc:LineExtensionAmount>')
                
                # Order line reference
                xml_parts.append('    <cac:OrderLineReference>')
                xml_parts.append(f'      <cbc:LineID>{idx}</cbc:LineID>')
                xml_parts.append('      <cac:OrderReference>')
                xml_parts.append(f'        <cbc:ID>{order_id}</cbc:ID>')
                xml_parts.append(f'        <cbc:SalesOrderID schemeID="VN">{data.get("sales_order_id", order_id)}</cbc:SalesOrderID>')
                xml_parts.append(f'        <cbc:IssueDate>{data.get("order_date", data.get("invoice_date", ""))}</cbc:IssueDate>')
                if data.get("customer_reference"):
                    xml_parts.append(f'        <cbc:CustomerReference>{data.get("customer_reference")}</cbc:CustomerReference>')
                xml_parts.append('      </cac:OrderReference>')
                xml_parts.append('    </cac:OrderLineReference>')
                
                # Pricing reference
                xml_parts.append('    <cac:PricingReference>')
                xml_parts.append('      <cac:AlternativeConditionPrice>')
                original_price = item.get('original_unit_price', item.get("unit_price", "0"))
                xml_parts.append(f'        <cbc:PriceAmount currencyID="{data.get("currency", "DKK")}">{self.format_amount(original_price)}</cbc:PriceAmount>')
                xml_parts.append('        <cbc:PriceTypeCode listID="UN/ECE 5387">AAB</cbc:PriceTypeCode>')
                xml_parts.append('      </cac:AlternativeConditionPrice>')
                xml_parts.append('    </cac:PricingReference>')
                
                # Tax total for line
                xml_parts.append('    <cac:TaxTotal>')
                xml_parts.append(f'      <cbc:TaxAmount currencyID="{data.get("currency", "DKK")}">{tax_amount}</cbc:TaxAmount>')
                xml_parts.append('      <cac:TaxSubtotal>')
                xml_parts.append(f'        <cbc:TaxableAmount currencyID="{data.get("currency", "DKK")}">{line_amount}</cbc:TaxableAmount>')
                xml_parts.append(f'        <cbc:TaxAmount currencyID="{data.get("currency", "DKK")}">{tax_amount}</cbc:TaxAmount>')
                xml_parts.append('        <cac:TaxCategory>')
                xml_parts.append('          <cbc:ID schemeAgencyID="320" schemeID="urn:oioubl:id:taxcategoryid-1.1">StandardRated</cbc:ID>')
                # xml_parts.append(f'          <cbc:Percent>{tax_percent}</cbc:Percent>')
                xml_parts.append(f'          <cbc:Percent>25.00</cbc:Percent>')
                xml_parts.append('          <cac:TaxScheme>')
                xml_parts.append('            <cbc:ID schemeAgencyID="320" schemeID="urn:oioubl:id:taxschemeid-1.1">63</cbc:ID>')
                xml_parts.append('            <cbc:Name>Moms</cbc:Name>')
                xml_parts.append('          </cac:TaxScheme>')
                xml_parts.append('        </cac:TaxCategory>')
                xml_parts.append('      </cac:TaxSubtotal>')
                xml_parts.append('    </cac:TaxTotal>')
                
                # Item details
                xml_parts.append('    <cac:Item>')
                description = item.get("description", f"Item {idx}")
                xml_parts.append(f'      <cbc:Description>{description}</cbc:Description>')
                xml_parts.append(f'      <cbc:Name>{description}</cbc:Name>')
                
                # Item identification
                item_number = item.get("item_number", "")
                if item_number:
                    xml_parts.append('      <cac:SellersItemIdentification>')
                    xml_parts.append(f'        <cbc:ID schemeID="SA">{item_number}</cbc:ID>')
                    xml_parts.append('      </cac:SellersItemIdentification>')
                
                # Add standard item identification (GTIN) if available
                gtin = item.get("gtin", item.get("ean", ""))
                if gtin:
                    xml_parts.append('      <cac:StandardItemIdentification>')
                    xml_parts.append(f'        <cbc:ID schemeID="GTIN">{gtin}</cbc:ID>')
                    xml_parts.append('      </cac:StandardItemIdentification>')
                
                # Add catalog item identification if available
                catalog_id = item.get("catalog_id", "")
                if catalog_id:
                    xml_parts.append('      <cac:CatalogueItemIdentification>')
                    xml_parts.append(f'        <cbc:ID schemeID="MP">{catalog_id}</cbc:ID>')
                    xml_parts.append('      </cac:CatalogueItemIdentification>')
                    
                xml_parts.append('    </cac:Item>')
                
                # Price - use discounted price if available
                discounted_price = item.get('discounted_unit_price', item.get("unit_price", "0"))
                xml_parts.append('    <cac:Price>')
                xml_parts.append(f'      <cbc:PriceAmount currencyID="{data.get("currency", "DKK")}">{self.format_amount(discounted_price)}</cbc:PriceAmount>')
                xml_parts.append(f'      <cbc:BaseQuantity unitCode="{unit}">1</cbc:BaseQuantity>')
                xml_parts.append('      <cbc:OrderableUnitFactorRate>1</cbc:OrderableUnitFactorRate>')
                xml_parts.append('    </cac:Price>')
                
                xml_parts.append('  </cac:InvoiceLine>')

            # Calculate sum of all line extension amounts
            line_extension_sum = sum(line_extension_amounts)
            logger.info(f"Sum of all line extension amounts: {line_extension_sum}")
            
            # Get the expected total
            expected_total = float(data.get("line_extension_amount", 0))
            logger.info(f"Expected line extension amount total: {expected_total}")
            
            # Check if there's a difference
            difference = expected_total - line_extension_sum
            logger.info(f"Difference in line extension amounts: {difference}")

            # If there's a small difference, adjust the last non-zero line
            if 0 < abs(difference) < 0.1:  # Allow for a small tolerance
                logger.info(f"Detected difference in line extension amounts: {difference}")
                
                # Find the last non-zero line extension amount
                adjustment_idx = -1
                for i in range(len(line_extension_amounts) - 1, -1, -1):
                    if line_extension_amounts[i] > 0.01:
                        adjustment_idx = i
                        break
                
                if adjustment_idx >= 0:
                    # Adjust the line to make the sum match
                    original_amount = line_extension_amounts[adjustment_idx]
                    corrected_amount = original_amount + difference
                    
                    logger.info(f"Adjusting line {adjustment_idx + 1} extension amount from {original_amount} to {corrected_amount}")
                    
                    # Fix the specific line in XML
                    line_idx = adjustment_idx + 1  # Line numbers are 1-based
                    
                    # Find the LineExtensionAmount element for this line
                    line_tag_start = f'  <cac:InvoiceLine>\n    <cbc:ID>{line_idx}</cbc:ID>'
                    amount_tag = f'<cbc:LineExtensionAmount currencyID="{data.get("currency", "DKK")}">{self.format_amount(original_amount)}</cbc:LineExtensionAmount>'
                    corrected_tag = f'<cbc:LineExtensionAmount currencyID="{data.get("currency", "DKK")}">{self.format_amount(corrected_amount)}</cbc:LineExtensionAmount>'
                    
                    # Join XML parts into a temporary string to do the replacement
                    xml_temp = '\n'.join(xml_parts)
                    
                    # Find the line and replace the amount
                    line_start_pos = xml_temp.find(line_tag_start)
                    if line_start_pos >= 0:
                        # Find the amount tag after the line start
                        amount_start_pos = xml_temp.find(amount_tag, line_start_pos)
                        if amount_start_pos >= 0:
                            # Replace the amount
                            xml_temp = xml_temp[:amount_start_pos] + corrected_tag + xml_temp[amount_start_pos + len(amount_tag):]
                            
                            # Update xml_parts (we need to regenerate from the string)
                            xml_parts = xml_temp.split('\n')
                            
                            logger.info(f"Successfully adjusted line {line_idx} extension amount in XML")
                        else:
                            logger.warning(f"Could not find amount tag for line {line_idx}")
                    else:
                        logger.warning(f"Could not find line {line_idx} in XML")
                else:
                    logger.warning("No suitable line found to adjust the difference")  
            
            # Close Invoice root element
            xml_parts.append('</Invoice>')
            
            # Join all parts into a single XML string
            xml_content = '\n'.join(xml_parts)
            
            return xml_content
    
        except Exception as e:
            logger.error(f"Error generating enhanced OIOXML: {e}", exc_info=True)
            return ""