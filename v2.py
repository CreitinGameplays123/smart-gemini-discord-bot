import discord
import aiofiles
import asyncio
from concurrent.futures import ThreadPoolExecutor
import random
import os
import io
import sys
from collections import defaultdict, deque
from bs4 import BeautifulSoup
import aiohttp
import datetime
from dotenv import load_dotenv
import json
import re
from groq import Groq
from gradio_client import Client

import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

load_dotenv() 

# Token and API keys
bot_token = os.getenv('TOKEN')
ai_key = os.getenv('GEMINI_KEY')
groq_token = os.getenv('GROQ_KEY')

# Some variables you might want to change.
SEARCH_SNIPPET_SIZE = 6000 # Website content max length size
MAX_CHAT_HISTORY_MESSAGES = 25 # Max number of messages that will be stored in chat history

# Get today's date and format it
today = datetime.datetime.now()
todayday = f'{today.strftime("%A")}, {today.month}/{today.day}/{today.year}'
todayhour = f'{today.hour}h:{today.minute}m:{today.second}s' # Unused 

# Base system prompt without web search results
# You can modify this system prompt as needed
base_system_prompt = f'''
You are Gemini, a large language model trained by Google AI, based on the "Gemini 1.5 Flash" model. You should always act like a helpful technical AI chatbot. We are interacting on a Discord chat. This means most of the time your lines should be a sentence or two, unless the user's request requires reasoning or long-form outputs.
You are operating within a Discord bot, and the bot developer is the user "creitingameplays". Chat history will be formatted like "discord_username: (message content)". Never put "discord_username: (message content)" in your answers.
Name: Gemini
Knowledge cutoff: October 26, 2023
Current date: {todayday}
You cannot provide the current time.
You can see usernames from the chat history.

## Your capabilities:
- Text Generation;
- Translation;
- Summarization;
- Question Answering;
- Web Search;
- Image Analysis;
- Audio Transcription and Answering.

# Tools

## Web Browser
Even though your knowledge cutoff is October 2023, you still can access more recent information using your web search feature, which uses DuckDuckGo Search Engine.
VERY IMPORTANT: You MUST ALWAYS use CITATION when using web search in your responses, in hyperlink format. Ensure you provide a citation for each paragraph that uses information from a web search.
If you don't know the answer to something, ask the user if they want you to research online about it.
### Citation Example
User: "What is the capital of France?"
You: "The capital of France is Paris. [1](https://en.wikipedia.org/wiki/Paris).
Paris is not only the capital of France but also its largest city. It is located in the north-central part of the country. [2](https://en.wikipedia.org/wiki/Paris)."

## Audio capability
If you don't understand the user's audio message, ask them to resend it.
IMPORTANT: Only analyze audios if the user explicitly asks you to do so.
You should also always prioritize the most recent audio message sent by the user, totally ignoring the chat history.
IMPORTANT: If the user sends an audio message, answer the question in it and don't just transcribe it. Think of it as if the audio is going directly to you and you need to answer its question.

## Image capability
Only analyze images if the user explicitly asks you to do so.
Always prioritize the most recent user-uploaded image.

Always respond with "I'm sorry but I cannot assist you with that." (or a similar message) if the user's message requests dangerous, harmful, adult content, sensitive topics or sexual activities.
'''

client = Client("https://devilent2-whisper-v3-zero.hf.space/--replicas/pvwp9/")

# This is for web search using audio message
async def user_audio(filename):
    try:
        job = client.submit(
            f"{filename}",
            "openai/whisper-large-v3",
            api_name="/predict"
        )
        while not job.done():
            await asyncio.sleep(0.1)
            
        response_text = job.result()
        return response_text 
        
    except Exception as e:
        print(f'Error: {e}')
        error_message = f'Transcription error: {e}'
        return error_message

# Restart function
async def restart_bot(): 
    os.execv(sys.executable, ['python'] + sys.argv)
    print('Restarted!')
    
# Split message function, fully written by GPT-4o
def split_msg(string, chunk_size=1500):
    chunks = []
    current_chunk = ""
    code_block_pattern = re.compile(r"```(\w+)?")
    current_lang = None
    in_code_block = False

    def add_chunk(chunk, close_code_block=False):
        if close_code_block and in_code_block:
            chunk += "" # ¯⁠\⁠_⁠(⁠ツ⁠)⁠_⁠/⁠¯
        chunks.append(chunk)

    lines = string.split('\n')
    for line in lines:
        match = code_block_pattern.match(line)
        if match:
            if in_code_block:
                # Closing an open code block
                current_chunk += line + "\n"
                add_chunk(current_chunk, close_code_block=True)
                current_chunk = ""
                in_code_block = False
                current_lang = None
            else:
                # Opening a new code block
                current_lang = match.group(1)
                if len(current_chunk) + len(line) + 1 > chunk_size:
                    add_chunk(current_chunk)
                    current_chunk = line + "\n"
                else:
                    current_chunk += line + "\n"
                in_code_block = True
        else:
            if len(current_chunk) + len(line) + 1 > chunk_size:
                if in_code_block:
                    add_chunk(current_chunk + "```", close_code_block=False)
                    current_chunk = f"```{current_lang}\n{line}\n"
                else:
                    add_chunk(current_chunk)
                    current_chunk = line + "\n"
            else:
                current_chunk += line + "\n"
    
    if current_chunk:
        if in_code_block:
            add_chunk(current_chunk)
        else:
            add_chunk(current_chunk)
        
    return chunks
    
# Use a dictionary to maintain chat history per channel
channel_histories = defaultdict(lambda: deque(maxlen=MAX_CHAT_HISTORY_MESSAGES))

async def save_chat_history(history_json, message):
    filename = history_json
    chat_history_by_channel = {}

    for channel_id, history in channel_histories.items():
        formatted_history = []
        
        for author, content in history:
            parts = [f'{author}: {content}']
                
            base_message = {
                'role': 'user' if author != 'Gemini' else 'model',
                'parts': parts
            }
            
            formatted_history.append(base_message)
            
        chat_history_by_channel[channel_id] = formatted_history

    async with aiofiles.open(filename, 'w') as f:
        await f.write(json.dumps(chat_history_by_channel, indent=4))
    await asyncio.sleep(0.1)    
        
# Load chat history from a file
def load_chat_history(filename='chat_history.json'):
    if os.path.exists(filename):
        with open(filename, 'r') as file:
            loaded_histories = json.load(file)
            for channel_id, messages in loaded_histories.items():
                for message in messages:
                    author = message["parts"][0].split(":")[0]
                    content = message["parts"][0].split(":", 1)[1].strip()
                    if (author, content) not in channel_histories[int(channel_id)]:
                        channel_histories[int(channel_id)].append((author, content))
                        
os.system('clear')
# Define the Discord bot
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
bot = discord.Client(intents=intents)

# Updated upload_and_save_file function
async def upload_and_save_file(attachment, channel_id):
    save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'attachments') 
    os.makedirs(save_dir, exist_ok=True)
    
    if attachment.content_type.startswith('audio'):
        filename = f'user_attachment_{channel_id}.ogg'
    elif attachment.content_type.startswith('image'):
        filename = f'user_attachment_{channel_id}.png'
    else:
        return None  # Skip unsupported file types
    filepath = os.path.join(save_dir, filename)

    async with aiofiles.open(filepath, 'wb') as f:
        await f.write(await attachment.read())
    
    return filepath    
    
# Updated generate_response function
async def generate_response(chat_history, system_prompt, channel_id, user_message, message):
    response = None
    genai.configure(api_key=ai_key)

    generation_config = {
        'temperature': 0.7,
        'top_p': 1.0,
        'top_k': 0,
        'max_output_tokens': 8192,
        'response_mime_type': 'text/plain',
    }

    model = genai.GenerativeModel(
        model_name='gemini-1.5-flash-latest',
        generation_config=generation_config,
        system_instruction=system_prompt,
        safety_settings={
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
    )

    chat_history_copy = list(channel_histories.get(channel_id, []))  # Make a copy of the deque for safe iteration

    async def upload_to_gemini(path, mime_type=None, cache={}):
        if path in cache:
            return cache[path]

        file = await asyncio.to_thread(genai.upload_file, path, mime_type=mime_type)  # Run the blocking upload in a separate thread
        cache[path] = file

        print(f'Uploaded file \'{file.display_name}\' as: {file.uri}')
        return file

    # Pre-upload files for the current channel
    attachment_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'attachments')
    file_path1 = os.path.join(attachment_folder, f'user_attachment_{channel_id}.png')
    file_path2 = os.path.join(attachment_folder, f'user_attachment_{channel_id}.ogg')

    files = None
    files2 = None
    
    inst_msg1 = "[Instructions: This is the last image. You should ignore this message and only use this as context. Respond to the user's message before this one. There is no audio in chat history yet.]"
    
    inst_msg2 = "[Instructions: This is the last audio. You should ignore this message and only use this as context. Respond to the user's message before this one. There is no image in chat history yet.]"
    
    inst_msg3 = "[Instructions: This is the last image and audio. You should ignore this message and only use this as context. Respond to the user's message before this one.]"
    
    if os.path.exists(file_path1):
        mime_type1 = 'image/png'
        files = await upload_to_gemini(file_path1, mime_type=mime_type1)
    
    if os.path.exists(file_path2):
        mime_type2 = 'audio/ogg'
        files2 = await upload_to_gemini(file_path2, mime_type=mime_type2)
    
    # Convert chat history to the desired format
    formatted_history = []
    for author, content in chat_history_copy:  # Iterate over the copied list
        formatted_history.append({
            'role': 'user' if author != 'Gemini' else 'model',
            'parts': [f'{author}: {content}'],
        })
    
    if files and not files2:
        formatted_history += [{
            'role': 'user',
            'parts': [
                files,
                f'{inst_msg1}',
            ],
        }]
    elif files2 and not files:
        formatted_history += [{
            'role': 'user',
            'parts': [
                files2,
                f'{inst_msg2}',
            ],
        }]
    elif files and files2:
        formatted_history += [{
            'role': 'user',
            'parts': [
                files2,
                files,
                f'{inst_msg3}',
            ],
        }]
    else:
        formatted_history += [{
            'role': 'user',
            'parts': [
                f'[Ignore this. There is no audio or image yet.]',
            ],
        }]
    
    # print(formatted_history)
    chat_session = await asyncio.to_thread(model.start_chat, history=formatted_history)
    response = await asyncio.to_thread(chat_session.send_message, user_message, stream=False)
    response = response.text
    return response
    
# check using Llama 3
async def needs_search(message_content, has_attachments, message):
    global search_rn
    client = Groq(api_key=groq_token)
    message_content[:2048]
    
    if has_attachments:
        attachment = message.attachments[0]
        if attachment.content_type.startswith('audio'):
            audio_transcription = await user_audio(attachment)
            message_content += f" [User message contains an audio, carefully analyze it - User audio transcription: {audio_transcription}]"
        else:
            message_content += " [User message contains an image - DO NOT web search]"
            
    completion = client.chat.completions.create(
        model='llama3-70b-8192',
        messages=[
            {
            "role": "system",
            "content": f"""
You are a helpful AI assistant called Gemini. Your knowledge cutoff date is October 2023. Today's date is {todayday}.
Your job is to decide when YOU need to do a web search based on chat history below. Chat history is a Discord chat between user and you (You are Gemini).
Please carefully analyze the conversation to determine if a web search is needed in order for you to provide an appropriate response to the lastest user message.
Also highly recommended searching in the following circumstances:
- User is asking YOU about current events or something that requires real-time information (weather, sports scores, etc.).
- User is asking YOU the latest information of something, means they want information until  {todayday}.
- User is asking YOU about some term you are totally unfamiliar with (it might be new).
- User explicitly asks YOU to browse or provide links to references.
Just respond with 'YES' or 'NO' if you think the following user chat history requires an internet search, don't say anything else than that.
If you believe a search will be necessary, skip a line and generate a search query that you would enter into the DuckDuckGo search engine to find the most relevant information to help you respond.
Use conversation history to get context for web searches.
Remember that every web search you perform is stateless, meaning you will need to search again if necessary.
The search query must also be in accordance with the language of the conversation (e.g Portuguese, English, Spanish etc.)
Keep it simple and short. Always output your search like this: SEARCH:example-search-query. Always put the `SEARCH`. Do not put any slashes in the search query. To choose a specific number of search results this will return, skip another line and put it like this: RESULTS:number, example: RESULTS:5. Always put the `RESULTS`, only works like that. Minimum of 3 and maximum of 15 search results. THIS IS REQUIRED. First is SEARCH, second is RESULTS.
You should NEVER do a web search if the user's message asks for dangerous, insecure, harmful, +18 (adult content), sexual content and malicious code. Just ignore these types of requests.
Respond with plain text only. Do not use any markdown formatting. Do not include any text before or after the search query. For normal searches, don't include the "site:".
Remember! today's date is {todayday}. Always keep this date in mind to provide time-relevant context in your search query.
Focus on generating the single most relevant search query you can think of to address the user's message. Do not provide multiple queries.
"""
            },
            {
                'role': 'user',
                'content': f'''
<conversation>
{message_content}
</conversation>
'''
            }
        ],
        temperature=0.6,
        max_tokens=1024,
        top_p=1.0,
        stream=False,
        stop=None,
    )
    
    output = completion.choices[0].message.content.strip()
    
    # check if its ok
    os.system("clear")
    print(output)
    print(message_content)
    # If the output suggests a search is needed, extract the search query
    if output.startswith('YES'):
        search_index = output.find('SEARCH:')
        if search_index != -1:
            search_query = output[search_index + len('SEARCH:'):].strip()
            print(f'Extracted search query: {search_query}')
            
        search_num = output.find('RESULTS:')
        if search_num != -1:
            search_rn = output[search_num + len('RESULTS:'):].strip()
            print(f"Extracted number of results: {search_rn}")
            
            return search_query, search_rn
    
    return None

async def search_duckduckgo(search_query):
    # search_query = search_query.replace(" ", "+").strip()
    url = f'https://html.duckduckgo.com/html/search?q={search_query}'
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:99.0) Gecko/20100101 Firefox/95.0'
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                return f'Error: Unable to fetch results (status code {response.status})'
            
            text = await response.text()
            soup = BeautifulSoup(text, 'html.parser')
            results = soup.find_all('a', class_='result__a')
            search_results = []
            for result in results:
                title = result.get_text()
                link = result['href']
                search_results.append({'title': title, 'link': link})

            return search_results

async def fetch_snippet(url, max_length=SEARCH_SNIPPET_SIZE):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:99.0) Gecko/20100101 Firefox/99.0'
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    return f'Error: Unable to fetch content from {url} (status code {response.status})'
                
                text = await response.text()
                soup = BeautifulSoup(text, 'html.parser')
                paragraphs = soup.find_all('p')
                content = ' '.join([para.get_text() for para in paragraphs])
                
                if len(content) > max_length:
                    return content[:max_length] + '...'
                else:
                    return content
    
    except Exception as e:
        return f'Error: Unable to fetch content from {url} ({str(e)})'

async def search(search_query):
    global search_rn
    search_rn = int(search_rn)
    results = await search_duckduckgo(search_query)
    results_output = []
    for i, result in enumerate(results[:search_rn]):
        snippet = await fetch_snippet(result['link'])
        result_str = f'{i+1}. Title: {result["title"]}\nLink: {result["link"]}\nSnippet: {snippet}\n'
        results_output.append(result_str)
        
    results_output_str = '\n'.join(results_output)
    
    print(results_output_str)
    return results_output_str
    
@bot.event
async def on_ready():
    load_chat_history()
    print(f'Logged in as {bot.user}!')

# List of IDs that can run the bot commands
allowed_ids = [
    775678427511783434, # creitin
    1205039741754671147 # meow
]

@bot.event
async def on_message(message):
    auto_respond_channel_id = 1252258977555943576 # Channel ID which the bot will auto respond / it can be the bot DMs for example
    channeID = message.channel.id
    
    if message.author == bot.user:
        return

    # restart process with a new pid
    if message.content.startswith('!k'):
        if message.author.id in allowed_ids:
            await message.reply(f'`{message.author.name}, Killing process and starting a new one...`')
            await asyncio.sleep(0.5)
            sys.exit(1)
        else:
            unauthorized = await message.reply(":x: You don't have permissions to run this command.")
            await asyncio.sleep(5)
            await unauthorized.delete()
            
    # Normal restart
    if message.content.startswith('!r'):
        if message.author.id in allowed_ids:
            await message.reply(f'`{message.author.name}, restarting bot...`')
            await asyncio.sleep(0.5)
            await restart_bot()
        else:
            unauthorized = await message.reply(":x: You don't have permissions to run this command.")
            await asyncio.sleep(5)
            await unauthorized.delete()
            
    # Delete chat history
    if message.content.startswith('!del'):
        if message.author.id in allowed_ids:
            try:
                with open("chat_history.json", "r") as f:
                    data = json.load(f)
                del data[f"{channeID}"]
                with open("chat_history.json", "w") as f:
                    json.dump(data, f, indent=4)
                await message.reply(f"`{message.author.name}, chat history deleted` :white_check_mark:")
                await restart_bot()
            except Exception as e:
                error = f"{e}"
                cID = f"{channeID}"
                print(f"`Error deleting chat history: {e}`")
                if cID in error:
                    await message.reply(f":x: `This channel's chat history is already deleted.`")
                else:
                    await message.reply(f":x: An error occurred: `{e}`")
        else:
            unauthorized = await message.reply(":x: You don't have permissions to run this command.")
            await asyncio.sleep(5)
            await unauthorized.delete()
            
    # Delete image
    if message.content.startswith('!imgdel'):
        if message.author.id in allowed_ids:
            try:
                os.remove(f"attachments/user_attachment_{channeID}.png")
                await message.reply(f"`{message.author.name}, image deleted` :white_check_mark:")
                await restart_bot()
            except Exception as e:
                print(f"`Error deleting image: {e}`")
                await message.reply(f":x: An error occurred: `{e}`")
        else:
            unauthorized = await message.reply(":x: You don't have permissions to run this command.")
            await asyncio.sleep(5)
            await unauthorized.delete()
            
    # Delete audio
    if message.content.startswith('!audiodel'):
        if message.author.id in allowed_ids:
            try:
                os.remove(f"attachments/user_attachment_{channeID}.ogg")
                await message.reply(f"`{message.author.name}, audio deleted` :white_check_mark:")
                await restart_bot()
            except Exception as e:
                print(f"`Error deleting audio: {e}`")
                await message.reply(f":x: An error occurred: `{e}`")
        else:
            unauthorized = await message.reply(":x: You don't have permissions to run this command.")
            await asyncio.sleep(5)
            await unauthorized.delete()
    
    # Help command
    if message.content.startswith('!h'):
        try:
            helpcmd = f"""
            ```
My commands:
- !del: Deletes the current channel chat history from JSON file.
- !k: Kills the bot process.
- !r: Restarts the bot.
- !imgdel: Deletes the current channel image from /attachments folder.
- !audiodel: Deletes the current channel audio from /attachments folder.
            
Experimental bot - Requested by {message.author.name} at {todayhour}. V2
            ```
            """
            msg = await message.reply(helpcmd)
            await asyncio.sleep(20)
            await msg.delete()
        except Exception as e:
                print(f"`Error: {e}`")
                await message.reply(f":x: An error occurred: `{e}`")
                
    # Main
    if bot.user in message.mentions or (message.reference and message.reference.resolved.author == bot.user) or message.channel.id == auto_respond_channel_id:
        await handle_message(message)

async def handle_message(message):
    bot_message = None
    try:
        history_json = 'chat_history.json'
        channel_id = message.channel.id
        channel_histories[channel_id].append((message.author.name, message.content))
        # save chat history
        await save_chat_history(history_json, message)
        
        # Check for attachments
        has_attachments = bool(message.attachments)
        attachment_task = None

        # Combine chat history for the current channel
        chat_history = '\n'.join([f'{author}: {content}' for author, content in channel_histories[channel_id]])
        chat_history = chat_history.replace(f'<@{bot.user.id}>', '').strip()

        async with message.channel.typing():
            await asyncio.sleep(1)
            bot_message = await message.reply('<a:generating:1246530696168734740> _ _')
            await asyncio.sleep(0.3)

        system_prompt = base_system_prompt

        user_message = message.content
        user_message = user_message.replace(f'<@{bot.user.id}>', '').strip()

        if has_attachments:
            # Save the first attachment locally in a separate task
            attachment = message.attachments[0]
            file_extension = os.path.splitext(attachment.filename)[1].lower()

            if file_extension in ['.png', '.jpg', '.jpeg', '.gif', '.mp3', '.wav', '.ogg']:
                attachment_task = asyncio.create_task(upload_and_save_file(attachment, channel_id))
                
                if file_extension in ['.png', '.jpg', '.jpeg', '.gif']:
                    user_message += ' [User message contains an image - analyze the last image]'
                else:
                    user_message += ' [User message contains an audio - listen to the last audio]'
                
            else:
                user_message += ' [User message contains an unsupported file type]'
        
        # You might want to replace the emojis below
        # Check if a web search is needed
        search_query = await needs_search(chat_history, has_attachments, message)
        if search_query:
            # lalalalalallalla schizophrenia
            var1 = f"{search_query}"
            var2 = var1.split("'")[1].split("RESULTS")[0]
            var2 = var2.replace("\n", "").strip()
            
            # Results number
            num_results = var1
            num_results = num_results.split("'")[1].split("RESULTS:")[1]
            
            await bot_message.edit(content=f'`Searching "{var2}"` <a:searchingweb:1246248294322147489>')
            await asyncio.sleep(3)
            await bot_message.edit(content=f'`Searching.` <a:searchingweb:1246248294322147489>')
            await asyncio.sleep(0.3)
            await bot_message.edit(content='`Searching..` <a:searchingweb:1246248294322147489>')
            results = await search(search_query)
            await bot_message.edit(content='`Searching...` <a:searchingweb:1246248294322147489>')
            await asyncio.sleep(0.3)
            await bot_message.edit(content='`Searching...` <:checkmark0:1246546819710849144>')
            await asyncio.sleep(0.3)
            await bot_message.edit(content=f'`Reading {num_results} results...` <a:searchingweb:1246248294322147489>')
            system_prompt += f'\nWeb search results: \n{results}'

        # Ensure the attachment task is finished if it was initiated
        if attachment_task:
            await attachment_task
        
        # Generate the response
        response = await generate_response(chat_history, system_prompt, channel_id, user_message, message)
        if response.startswith("Gemini:"): # Gemini has an annoying habit of starting their responses with its own name >:(
            response = response.replace("Gemini:", "", 1).strip()
        
        await bot_message.edit(content='<a:generating:1246530696168734740> _ _')
        await asyncio.sleep(0.5)

        response_chunks = split_msg(response, chunk_size=1500)
        for chunk_index, chunk in enumerate(response_chunks):
            streaming_response = ''
            batch_size = random.randint(80, 100)
            for i in range(0, len(chunk), batch_size):
                streaming_response += chunk[i:i + batch_size]
                await bot_message.edit(content=streaming_response + ' <a:generatingslow:1246630905632653373> ')
                await asyncio.sleep(0.3)

            await bot_message.edit(content=streaming_response)
            if chunk_index < len(response_chunks) - 1:
                bot_message = await message.reply('<a:generatingslow:1246630905632653373>  _ _')
                
            # Append the bot's message to the chat history
            channel_histories[channel_id].append(('Gemini', streaming_response))

            await save_chat_history(history_json, message)
            
    except Exception as e:
        print(f'Error handling message: {e}')
        if bot_message:
            await bot_message.edit(f'An error occurred: `{e}`')
        await asyncio.sleep(6)
        await bot_message.delete()
        
# Start the bot with your token
try:
    bot.run(bot_token)
except Exception as e:
    print(f'Error starting the bot: {e}')
    
# damn this code is big :skull: - Creitin
