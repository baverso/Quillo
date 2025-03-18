#!/usr/bin/env python3
"""
orchestrator_text_completions.py

This script serves as the central orchestrator for the EVQLV AI Email Management System.
It integrates the retrieval and parsing modules along with various LangChain agent chains 
to process incoming emails. This version uses traditional PromptTemplate with text completions.

Author: Brett Averso
Date: March 13, 2025
License: GPL-3.0
"""

import os
import json
import logging

from langchain_openai import OpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain.output_parsers import OutputFixingParser

# Import retrieval and parsing modules
from tools.email_retriever import EmailRetriever
from tools.email_parser import EmailParser
from libs.api_manager import setup_openai_api
from tools.human_feedback import get_yes_no_feedback
from tools.email_archiver import archive_email

# Import Pydantic models. Writers do not get Pydantic models.
from agents.models import (
    NeedsResponseOutput,
    EmailSummaryOutput,
    EmailCategoryOutput,
    MeetingRequestOutput,
    EditorAnalysisOutput
)

# Silence all loggers
logging.getLogger().setLevel(logging.ERROR)  # Root logger configuration
# Or to completely disable all logging:
# logging.disable(logging.CRITICAL)

# Configure logging
logger = logging.getLogger(__name__)

# Load API keys and set up OpenAI API
setup_openai_api()

# =====================
# Load Agent Prompts
# =====================
def load_prompt(file_path):
    """
    Load a prompt from a text file, escape curly braces, and clean up any meta-instructions.

    Args:
        file_path (str): Path to the prompt file.

    Returns:
        str: The cleaned content of the prompt file with curly braces escaped.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
        
        # Remove any meta-instructions about the prompt format
        if content.startswith("Below is your revised prompt file"):
            # Find the first occurrence of "Role:" and start from there
            role_index = content.find("Role:")
            if role_index != -1:
                content = content[role_index:]
        
        # # Escape curly braces by doubling them
        content = content.replace("{", "{{").replace("}", "}}")
        return content

# Directory containing prompt files
PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts")

# =====================
# Load Agent Prompts
# =====================
summarizer_prompt_text = load_prompt(os.path.join(PROMPTS_DIR, "email_summarizer_prompt.txt"))
needs_response_prompt_text = load_prompt(os.path.join(PROMPTS_DIR, "email_needs_response_prompt.txt"))
categorizer_prompt_text = load_prompt(os.path.join(PROMPTS_DIR, "email_categorizer_prompt.txt"))
meeting_request_decider_prompt_text = load_prompt(os.path.join(PROMPTS_DIR, "meeting_request_decider_prompt.txt"))
decline_writer_prompt_text = load_prompt(os.path.join(PROMPTS_DIR, "decline_writer_prompt.txt"))
schedule_email_writer_prompt_text = load_prompt(os.path.join(PROMPTS_DIR, "schedule_email_writer_prompt.txt"))
email_writer_prompt_text = load_prompt(os.path.join(PROMPTS_DIR, "email_writer_prompt.txt"))
editor_prompt_text = load_prompt(os.path.join(PROMPTS_DIR, "email_editor_agent_prompt.txt"))


# =====================
# Define Agent Model(s) and pydantic Output Parsers
# =====================

# Create base models with different capabilities
base_model = OpenAI(temperature=0, model="gpt-3.5-turbo-instruct")

# Create a JSON output parser for each Pydantic model
summarizer_parser = JsonOutputParser(pydantic_object=EmailSummaryOutput)
needs_response_parser = JsonOutputParser(pydantic_object=NeedsResponseOutput)
categorizer_parser = JsonOutputParser(pydantic_object=EmailCategoryOutput)
meeting_request_parser = JsonOutputParser(pydantic_object=MeetingRequestOutput)
editor_parser = JsonOutputParser(pydantic_object=EditorAnalysisOutput)

# Create fixing parsers to handle potential parsing errors
summarizer_fixing_parser = OutputFixingParser.from_llm(parser=summarizer_parser, llm=base_model)
needs_response_fixing_parser = OutputFixingParser.from_llm(parser=needs_response_parser, llm=base_model)
categorizer_fixing_parser = OutputFixingParser.from_llm(parser=categorizer_parser, llm=base_model)
meeting_request_fixing_parser = OutputFixingParser.from_llm(parser=meeting_request_parser, llm=base_model)
editor_fixing_parser = OutputFixingParser.from_llm(parser=editor_parser, llm=base_model)

# =====================
# Define Agent Chains
# =====================

# EMAIL_SUMMARIZER: Summarizes the incoming email into a structured summary.
summarizer_prompt = PromptTemplate(
    template=summarizer_prompt_text + "\n\nEmail content: {email_content}\n\nProvide the output in JSON format.",
    input_variables=["email_content"]
)
summarizer_chain = summarizer_prompt | base_model | summarizer_fixing_parser

# NEEDS_RESPONSE: Determines if the email needs a response.
needs_response_prompt = PromptTemplate(
    template=needs_response_prompt_text + "\n\nEmail summary: {summary}\n\nProvide the output in JSON format.",
    input_variables=["summary"]
)
needs_response_chain = needs_response_prompt | base_model | needs_response_fixing_parser 

# CATEGORIZER: Categorizes the email.
categorizer_prompt = PromptTemplate(
    template=categorizer_prompt_text + "\n\nEmail content: {email_content}\n\nProvide the output in JSON format.",
    input_variables=["email_content"]
)
categorizer_chain = categorizer_prompt | base_model | categorizer_fixing_parser

# MEETING_REQUEST_DECIDER: Determines if the email is solely a meeting request.
meeting_request_decider_prompt = PromptTemplate(
    template=meeting_request_decider_prompt_text + "\n\nEmail content: {email_content}\n\nProvide the output in JSON format.",
    input_variables=["email_content"]
)
meeting_request_decider_chain = meeting_request_decider_prompt | base_model | meeting_request_fixing_parser  # Using reasoning_model

# DECLINE_WRITER: Writes a polite email declining the request.
decline_writer_prompt = PromptTemplate(
    template=decline_writer_prompt_text + "\n\nEmail content: {email_content}",
    input_variables=["email_content"]
)
decline_writer_chain = decline_writer_prompt | base_model 

# SCHEDULE_WRITER: Writes a response to schedule a meeting.
schedule_writer_prompt = PromptTemplate(
    template=schedule_email_writer_prompt_text + "\n\nEmail content: {email_content}",
    input_variables=["email_content"]
)
schedule_email_writer_chain = schedule_writer_prompt | base_model  

# EMAIL_WRITER: Writes a response to the email.
email_writer_prompt = PromptTemplate(
    template=email_writer_prompt_text + "\n\nEmail content: {email_content}",
    input_variables=["email_content"]
)
email_writer_chain = email_writer_prompt | base_model  

# EDITOR: Analyzes the differences between the draft email and the edited version.
editor_prompt = PromptTemplate(
    template=editor_prompt_text + "\n\nDraft email: {draft_email}\nEdited email: {edited_email}\n\nProvide the output in JSON format.",
    input_variables=["draft_email", "edited_email"]
)
editor_chain = editor_prompt | base_model | editor_fixing_parser  # Using reasoning_model


def orchestrate_email_response():
    """
    Orchestrates the processing of incoming emails by retrieving raw email threads,
    parsing them into a structured format, and then passing them through LangChain agents
    to generate the appropriate email response.

    Returns:
        str or dict: The final email response or, if human-edited feedback exists,
                     a dict containing both the final response and the editor analysis.
    """
    try:
        # Retrieve raw emails using EmailRetriever.
        retriever = EmailRetriever()
        threads = retriever.retrieve_emails(n=50)

        # Parse raw emails using EmailParser.
        parser = EmailParser(retriever.service)
        structured_emails = parser.parse_emails(threads)
        
        # Filter for the most recent emails (order=1)
        recent_emails = [email for email in structured_emails if email.get('order') == 1]
        
        # Check if we have any emails to process
        if not recent_emails:
            logger.info("No recent emails (order=1) found to process.")
            return "No emails to process."
        
        # Create a list to store responses for all processed emails
        all_responses = []
        
        # Process each recent email
        for i, email_data in enumerate(recent_emails):
            print('Processing email {} of {}'.format(i+1, len(recent_emails)))
            # Get the message_data if it exists, otherwise use the email_data directly
            if 'message_data' in email_data:
                message_data = email_data['message_data']
            else:
                message_data = email_data
            
            # Store the message ID for later use with labels
            message_id = email_data.get('messageId')
            
            logger.info("Starting email summarization.")
            # Convert message_data to JSON string for the LLM
            email_content_input = json.dumps(message_data)
            
            # Use the chain directly - now returns a JSON dictionary
            summary_result = summarizer_chain.invoke({"email_content": email_content_input})
            logger.info("Email summarization complete.")

            logger.info("Determining if a response is needed.")
                
            # Get structured output from the JSON parser
            needs_response_output = needs_response_chain.invoke({"summary": str(summary_result)})
            needs_response = needs_response_output["needs_response"]
            logger.info("Needs response decision: %s", needs_response)
            
            # Get human feedback on the needs_response decision using our new function
            is_correct, human_input = get_yes_no_feedback(
                prompt="Is this decision correct?",
                decision=needs_response,
                context=summary_result
            )
            
            # Handle the four conditional cases
            if needs_response == "respond" and is_correct:
                # AI decided to respond and human agrees
                logger.info("Human confirmed AI decision to respond.")
                # Continue with response generation
            elif needs_response == "respond" and not is_correct:
                # AI decided to respond but human disagrees
                logger.info("Human overrode AI decision to respond. No response will be sent.")
                # Archive the email by removing INBOX label and adding NO RESPONSE NEEDED label
                if message_id:
                    logger.info("Archiving email with message ID: %s", message_id)
                    archive_email(retriever.service, message_id)
                print("Human decided no response is needed. Email archived.")
                continue
            elif needs_response == "no response needed" and is_correct:
                # AI decided not to respond and human agrees
                logger.info("Human confirmed AI decision not to respond.")
                # Archive the email by removing INBOX label and adding NO RESPONSE NEEDED label
                if message_id:
                    logger.info("Archiving email with message ID: %s", message_id)
                    archive_email(retriever.service, message_id)
                print("No response needed. Email archived.")
                continue
            elif needs_response == "no response needed" and not is_correct:
                # AI decided not to respond but human disagrees
                logger.info("Human overrode AI decision not to respond. Will generate a response.")
                # Continue with response generation
            
            # If we reach here, we need to generate a response (either AI decided or human overrode)
            logger.info("Categorizing the email.")
            # Get structured output from the JSON parser
            category_output = categorizer_chain.invoke({"email_content": email_content_input})
            category = category_output["decision"]
            logger.info("Email categorizer decision: %s", category)
            
            # Get human feedback on the category decision
            is_category_correct, _ = get_yes_no_feedback(
                prompt="Is this category correct?",
                decision=category,
                context=None
            )
            
            # Handle the four conditional cases for categorization
            if category == "decline" and is_category_correct:
                # Categorizer decided to decline and human agrees
                logger.info("Human confirmed decision to decline. Moving to decline email agent.")
                final_response = decline_writer_chain.invoke({"email_content": email_content_input})
            elif category == "decline" and not is_category_correct:
                # Categorizer decided to decline but human disagrees
                logger.info("Human overrode decision to decline. Updating to move forward.")
                # Update category to move forward
                category = "move forward"
                logger.info("Proceeding to meeting request decider...")
                # Continue to meeting request decider
            elif category != "decline" and is_category_correct:
                # Categorizer decided to move forward and human agrees
                logger.info("Human confirmed decision to move forward. Proceeding to meeting request decider.")
                # Continue to meeting request decider
            elif category != "decline" and not is_category_correct:
                # Categorizer decided to move forward but human disagrees
                logger.info("Human overrode decision to move forward. Updating to decline.")
                # Update category to decline
                category = "decline"
                logger.info("Moving to decline email agent.")
                final_response = decline_writer_chain.invoke({"email_content": email_content_input})
            
            # If we reach here, we need to proceed to the meeting request decider
            if category != "decline":
                logger.info("Determining if the email is solely a scheduling request.")
                    
                # Get structured output from the JSON parser
                import pprint
                print('--------------------------------')
                print('--------------------------------')
                pprint.pprint(email_content_input)
                print('--------------------------------')
                print('--------------------------------')                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     
                meeting_output = meeting_request_decider_chain.invoke({"email_content": email_content_input})
                is_meeting_request = meeting_output["decision"]
                logger.info("Meeting request decision: %s", is_meeting_request)
                
                # Get human feedback on meeting request decision
                is_meeting_correct, _ = get_yes_no_feedback(
                    prompt="Is this meeting request decision correct?",
                    decision=f"Is meeting request? {is_meeting_request}",
                    context=f"Email category: {category}"
                )
                
                if not is_meeting_correct:
                    # Invert the meeting request decision if human disagrees
                    is_meeting_request = "yes" if is_meeting_request not in ["yes", "true", "1"] else "no"
                    logger.info(f"Human corrected meeting request decision to: {is_meeting_request}")
                
                if is_meeting_request in ["yes", "true", "1"]:
                    final_response = schedule_email_writer_chain.invoke({"email_content": email_content_input})

                else:
                    final_response = email_writer_chain.invoke({"email_content": email_content_input})

            # Optionally, if human-edited feedback is provided, analyze differences.
            if "edited_email" in message_data and message_data["edited_email"].strip():
                editor_analysis = editor_chain.invoke({
                    "draft_email": final_response,
                    "edited_email": message_data["edited_email"]
                })
                
                # Editor analysis is now a JSON dictionary
                response = {
                    "final_response": final_response,
                    "editor_analysis": editor_analysis
                }
            else:
                # Ask if the user wants to edit the response
                wants_to_edit, _ = get_yes_no_feedback(
                    prompt="Would you like to edit this response before sending?",
                    context=f"Generated response:\n{final_response}"
                )
                
                if wants_to_edit:
                    print("\nPlease edit the response below:")
                    print("-" * 50)
                    print(final_response)
                    print("-" * 50)
                    edited_response = input("Enter your edited response (or press Enter to keep as is):\n")
                    
                    if edited_response.strip():
                        # If user provided an edited response, analyze the differences
                        editor_analysis = editor_chain.invoke({
                            "draft_email": final_response,
                            "edited_email": edited_response
                        })
                        
                        response = {
                            "final_response": edited_response,
                            "editor_analysis": editor_analysis
                        }
                
            # Instead of returning, append the response to all_responses
            # Add the response to all_responses
            all_responses.append(response)
            
        # Return all collected responses
        return all_responses if len(all_responses) > 0 else "No responses generated."


    except Exception as e:
        logger.error("Error in orchestrating email response: %s", str(e))
        raise

def main():
    """
    Main function to run the orchestrator.
    """
    response = orchestrate_email_response()
    
    if isinstance(response, dict):
        print("\nFinal Email Response:")
        print(response["final_response"])
        print("\nEditor Analysis:")
        print(response["editor_analysis"])
    else:
        print("\nFinal Email Response:")
        print(response)

if __name__ == "__main__":
    main() 