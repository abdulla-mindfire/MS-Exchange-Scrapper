"""
The configuration file would look like this (sans those // comments):

{
    "authority": "https://login.microsoftonline.com/Enter_the_Tenant_Name_Here",
    "client_id": "your_client_id",
    "scope": ["https://graph.microsoft.com/.default"],
        // For more information about scopes for an app, refer:
        // https://docs.microsoft.com/en-us/azure/active-directory/develop/v2-oauth2-client-creds-grant-flow#second-case-access-token-request-with-a-certificate"

    "secret": "The secret generated by AAD during your confidential app registration",
        // For information about generating client secret, refer:
        // https://github.com/AzureAD/microsoft-authentication-library-for-python/wiki/Client-Credentials#registering-client-secrets-using-the-application-registration-portal

    "endpoint": "https://graph.microsoft.com/v1.0/users"

}

You can then run this sample with a JSON configuration file:

    python sample.py parameters.json
"""

import sys  # For simplicity, we'll read config file from 1st CLI param sys.argv[1]
import json
import logging
import re
import io
import os
import xlrd
import csv
import base64
import zipfile
import xml.etree.ElementTree as ET
from operator import itemgetter
from datetime import datetime
from time import time

import pandas as pd
from openpyxl import load_workbook
import requests
import msal

now = datetime.now() # current date and time
date = now.strftime("%m-%d-%Y")

logging.basicConfig(
        filename = f'log/{date}.csv', 
        level = logging.INFO, 
        format = '%(levelname)s:%(asctime)s,%(message)s',
        datefmt='%m/%d/%Y %I:%M:%S %p')

  
def timer_func(func):
    # This function shows the execution time of 
    # the function object passed
    def wrap_func(*args, **kwargs):
        start_time = time()
        result = func(*args, **kwargs)
        end_time = time()
        print(f'Function {func.__name__!r} executed in {(end_time - start_time):.4f}s')
        return result
    return wrap_func

EMAIL_REGEX = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
 
# Define a function for validating an Email
def check_email(email):
    if(re.fullmatch(EMAIL_REGEX, email)):
        return True
    else:
        return False

class MailExchangeScrappper:
    '''
    MailExchangeScrapper class to get the user details and their e-mails from
    their mailbox.
    Attachment to get from emails check the SSN from email body and attachments
    too having the files with extension (xlsx, xls, docx, csv)
    @params => config file in parameter.json
    '''
    extract_ssn_pattern = "(?!666|000|9\\d{2})\\d{3}-(?!00)\\d{2}-(?!0{4})\\d{4}"
    extract_ssn_pattern_from_file = "([\(])?\d{3,4}([- \)])?(\s)?\d{2}([- \s])?\d{3,4}([-])?(\d{2})?"
    allowed_extenstion = ['csv', 'docs', 'docx', 'xlsx', 'xls']
    current_dir = os.getcwd()
    email_messages_data = {}


    def __init__(self, config) -> None:
        self.config = config
        self.result = None
        self.user_details = {}
        self.user_messages = {}
        self.mail_folders = {}
        self.folders_names_lists = []
        self.folders_names_details = []
        self.folder_with_childrens_list = []
        self.message_ids_contains_attachment = []
        self.attachment_data_of_messages_by_id = []
        self.attachment_found_file_list_extension = []
        self.attachment_found_file_name = []
        self.email_parent_folder = {}
        self.next_page = None
        self.total_email_count = len(os.listdir(f"{self.current_dir}/emailData"))
        self.long_lived_token()

    def check_token_status(self):
        '''
        To check the status of token if it is correctly set by method long_lived_token()
        :return: Bool
        '''
        if self.result is not None and "access_token" in self.result:
            return True
        return False

    def check_ssn_regex(self, body):
        '''
        Regex checking for SSN in email Body
        :return: Bool
        '''
        data = re.search(self.extract_ssn_pattern, body)
        if data:
            return True
        return False


    @timer_func
    def long_lived_token(self):
        '''
        Generate Long Lived Token from MS Graph API or from cache
        :return: Dict with access token and expiry
        '''
        try:
            # Create a preferably long-lived app instance which maintains a token cache.
            app = msal.ConfidentialClientApplication(
                self.config["client_id"], authority=self.config["authority"],
                client_credential=self.config["secret"],
                # token_cache=...  # Default cache is in memory only.
                                # You can learn how to use SerializableTokenCache from
                                # https://msal-python.rtfd.io/en/latest/#msal.SerializableTokenCache
                )

            # The pattern to acquire a token looks like this.
            # Firstly, looks up a token from cache
            # Since we are looking for token for the current app, NOT for an end user,
            # notice we give account parameter as None.
            self.result = app.acquire_token_silent(self.config["scope"], account=None)

            if not self.result:
                print("No suitable token exists in cache. Let's get a new one from AAD.")
                self.result = app.acquire_token_for_client(scopes=self.config["scope"])
            print("Token Set")
        except Exception as e:
            print("Failed to Get Token", str(e))

    @timer_func
    def get_user_details_by_email(self, email):
        '''
        This method take user email as params and call the MS graph api and get 
        back the user related information like name, email, id etc.
        @params: str (email)
        :return: Dict | containing user information
        '''
        if self.check_token_status:
            graph_data = requests.get(  # Use token to call downstream service
            self.config["endpoint"] + email,
            headers={'Authorization': 'Bearer ' + self.result['access_token']}, )
            print("Graph API call result: ")
            if(graph_data.status_code == 200):
                self.user_details = graph_data.json()
                print(self.user_details)
            elif graph_data.status_code == 404:
                logging.info(f"Something went wrong with the email: {email} While fetching user, {graph_data.json()}")
            else:
                logging.info(f"Something went wrong with the email: {email}, {graph_data.json()}")
        else:
            print("===========Error in getting user data ==========")
    
    @timer_func
    def get_mailfolder(self, folder_id):
        '''
        This method take user email parentFolder id as params and call the MS 
        graph api and get back the parent folder related information like 
        name, id etc.
        @params: str (parentFolderId)
        :return: Dict | containing Folder information
        '''
        graph_data = requests.get(  # Use token to call downstream service
            self.config["endpoint"] + self.user_details['id'] + "/mailFolders/" + folder_id + "?includeHiddenFolders=true&$select=displayName",
            headers={'Authorization': 'Bearer ' + self.result['access_token']}, ).json()
        
        self.email_parent_folder = graph_data

    @timer_func
    def get_email_messages_of_user(self):
        '''
        This method take user id from instance variable and call the MS 
        graph api and get back all the email details
        And check the SSN in body of Every email
        '''
        if self.check_token_status:
            if self.next_page is None:
                self.email_messages_data = requests.get(  # Use token to call downstream service
                self.config["endpoint"] + self.user_details['id'] + "/messages",
                headers={'Authorization': 'Bearer ' + self.result['access_token']}, ).json()
            else:
                self.email_messages_data = requests.get(self.next_page, headers={'Authorization': 'Bearer ' + self.result['access_token']},).json()
            print("Graph API call result: ")

            if "@odata.nextLink" in self.email_messages_data:
                self.next_page = self.email_messages_data['@odata.nextLink']
            else:
                self.next_page = None
            
            # empty_data = {"value": []}
            for item in self.email_messages_data['value']:
                try:
                    body = item['bodyPreview']
                    self.get_mailfolder(item['parentFolderId'])
                    if self.check_ssn_regex(body): 
                        logging.info(f"{self.email_parent_folder['displayName']} , {item['sender']['emailAddress']['address']} , {item['toRecipients'][0]['emailAddress']['address']} , {item['subject']} , {item['receivedDateTime']}")
                except Exception as e:
                    logging.info(f"Something went wrong while processing mailbox email continue with next message, {str(e)}")
                    continue

            if self.next_page:
                self.get_email_messages_of_user()
        else:
            print("===========Error in getting user messages ==========")


    def get_all_attachment_by_message_id(self, message_id):
        if self.check_token_status:
            graph_data = requests.get(  # Use token to call downstream service
            self.config["endpoint"] + self.user_details['id'] + "/messages/" + message_id + "/attachments/",
            headers={'Authorization': 'Bearer ' + self.result['access_token']}, ).json()
           
            self.attachment_data_of_messages_by_id = graph_data['value']
            self.create_files_via_content_bytes()
        else:
            print("===========Error in getting mail folders ==========")

    def create_files_via_content_bytes(self):
        self.attachment_found_file_name = []
        self.attachment_found_file_list_extension = []
        for item in self.attachment_data_of_messages_by_id:
            self.attachment_found_file_name.append(item['name'])
            file_type = item['name'].split('.')[-1]
            if file_type in self.allowed_extenstion:
                filename = item['name']
                bytesdata = bytes(item['contentBytes'], 'utf-8')
                xlDecoded = base64.b64decode(bytesdata)
                
                with open(f'temp/{filename}.{file_type}', 'wb') as f:
                    f.write(xlDecoded)
                
                path = f'temp/{filename}.{file_type}'
                self.check_attachment_content_ssn(file_type, path)
                self.remove_file(path)


    def remove_file(self, path):
        if os.path.exists(path):
            os.remove(path)
        else:
            print("The file does not exist")
    
    def check_attachment_content_ssn(self, file_type, path):
        df = pd.DataFrame()
        if file_type == 'csv':
            df = pd.read_csv(path)
            '''Converts pandas dataframe into one list'''
            list_df = [cell for cell in [df[i] for i in df]]
            text = ' '.join([str(x) for x in list_df])
            pattern_match = re.compile(self.extract_ssn_pattern_from_file)
            matches = pattern_match.finditer(text)
            total_matches = list(matches)
            if len(total_matches) > 0:
                # save to actual dir
                self.attachment_found_file_list_extension.append('CSV')
                print("Found CSV: ", len(total_matches))
        elif file_type in ['xlsx', 'xls']:
            wb = load_workbook(path)
            #convert sheets to pandas dataframe
            #combine all sheets into one dataframe
            frames = [pd.DataFrame(sheet.values) for sheet in wb.worksheets]
            df = pd.concat(frames)
            wb.close()

            '''Converts pandas dataframe into one list'''
            list_df = [cell for cell in [df[i] for i in df]]
            text = ' '.join([str(x) for x in list_df])
            pattern_match = re.compile(self.extract_ssn_pattern_from_file)
            matches = pattern_match.finditer(text)
            total_matches = list(matches)
            if len(total_matches) > 0:
                # save to actual dir
                self.attachment_found_file_list_extension.append('Excel')
                print("Found XLSX: ", len(total_matches))
        elif file_type == 'docx':
            doc = zipfile.ZipFile(path).read('word/document.xml')
            root = ET.fromstring(doc)
            string_doc = ET.tostring(root)
            pattern_match = re.compile(self.extract_ssn_pattern_from_file)
            matches = pattern_match.finditer(string_doc.decode('utf-8'))
            total_matches = list(matches)
            if len(total_matches) > 0:
                # save to actual dir
                self.attachment_found_file_list_extension.append('Doc')
                print("Found DOC: ", len(total_matches))


    def set_folder_names_list(self, folders):
        folder_with_childrens = []
        empty_list = []
        for item in folders['value']:
            empty_list.append({
                "id": item['id'],
                "name": item['displayName']
            })
            if item['childFolderCount'] > 0:
                folder_with_childrens.append(item['id'])

        self.folders_names_lists = list(map(itemgetter('displayName'), folders['value']))
        self.folders_names_details = empty_list
        self.folder_with_childrens_list = folder_with_childrens


    def get_user_mail_folders(self):
        if self.check_token_status:
            graph_data = requests.get(  # Use token to call downstream service
            self.config["endpoint"] + self.user_details['id'] + "/mailFolders/?includeHiddenFolders=true",
            headers={'Authorization': 'Bearer ' + self.result['access_token']}, ).json()
            print("Graph API call result: ")
            self.set_folder_names_list(graph_data)
            self.mail_folders = json.dumps(graph_data, indent=2)
            self.get_mail_by_folder_name()

        else:
            print("===========Error in getting mail folders ==========")

    def get_folder_id(self, folder_name):
        id = ""
        name = ""
        for item in self.folders_names_details:
            if folder_name == item['name']:
                id = item['id']
                name = item['name']
                break
        return id, name

    def get_mail_by_folder_name(self):
        print(self.folders_names_lists)
        folder_name = input("Please enter the folder name from above list: ")
        
        if folder_name in self.folders_names_lists:
            id, name = self.get_folder_id(folder_name)
            print(name, id)
            graph_data = requests.get(  # Use token to call downstream service
            self.config["endpoint"] + self.user_details['id'] + "/mailFolders/" + str(id) + "/messages",
            headers={'Authorization': 'Bearer ' + self.result['access_token']}, ).json()
            print("Graph API call result: ")
            self.get_nested_folders(id)
        elif folder_name == "Q" or folder_name == 'q':
            sys.exit()
        else:
            print("Please enter correct folder Name or press Q to exit")
            self.get_mail_by_folder_name()
        
    def get_nested_folders(self, id):
        if self.check_token_status:
            graph_data = requests.get(  # Use token to call downstream service
            self.config["endpoint"] + self.user_details['id'] + "/mailFolders/" + id + "/childFolders?includeHiddenFolders=true",
            headers={'Authorization': 'Bearer ' + self.result['access_token']}, ).json()
            print("Graph API call result: ")
            print(json.dumps(graph_data, indent=2))
        else:
            print("===========Error in getting nested mail folders ==========")


if __name__ == "__main__":
    try:
        config = json.load(open(sys.argv[1]))
        email_csv = open(sys.argv[2])
    except Exception as e:
        print("Please add parameter.json for config as argument 1 and email_csv file as args 2 !")
        sys.exit()

    scrapper = MailExchangeScrappper(config=config)
    with open(email_csv.name, 'r') as file:
        reader = csv.reader(file)
        fields = next(reader)
        for row in reader:
            try:
                start_time = time()
                if check_email(row[0]):
                    scrapper.get_user_details_by_email(row[0])
                    scrapper.get_email_messages_of_user()
                else:
                    print(f"Email: {row[0]} is not valid")
                    continue
                end_time = time()
                print(f'Whole Script executed in {(end_time - start_time):.4f}s')
            except Exception as e:
                print("Email: ", row[0])
                print("Something went wrong with Exception: ", e)
                print("Continue to next email")
                continue
