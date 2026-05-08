import requests
import time
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import re
import wikipediaapi
from plexapi.server import PlexServer
from bs4 import BeautifulSoup


def add_label_to_card(card_id, label_id):
    url = f"https://api.trello.com/1/cards/{card_id}/idLabels"
    query = {'key': TRELLO_KEY, 'token': TRELLO_TOKEN, 'value': label_id}
    requests.post(url, params=query)

def add_comment_to_card(card_id, comment):
    url = f"https://api.trello.com/1/cards/{card_id}/actions/comments"
    query = {'key': TRELLO_KEY, 'token': TRELLO_TOKEN, 'text': comment}
    requests.post(url, params=query)

def check_plex_title(title, expected_episodes=None):
    plex = PlexServer(PLEX_URL, PLEX_TOKEN)
    # Search in Movies
    movies = plex.library.section('Movies').search(title=title)
    if movies:
        return {'type': 'movie', 'found': True}
    # Search in TV Shows
    shows = plex.library.section('TV Shows').search(title=title)
    if shows:
        show = shows[0]
        episode_count = sum(len(season.episodes()) for season in show.seasons())
        match = (expected_episodes is None) or (episode_count == expected_episodes)
        return {'type': 'series', 'found': True, 'episode_count': episode_count, 'matches': match}
    return {'found': False}

def clean_description(desc):
    # Remove old IMDb Info blocks
    desc = re.sub(r'\n*\n?IMDb Info:.*?(?=\n{2,}|$)', '', desc, flags=re.DOTALL)
    # Remove any standalone Episodes, IMDb Rating, or Genre lines
    desc = re.sub(r'\nEpisodes:\s*\d+', '', desc)
    desc = re.sub(r'\nIMDb Rating:\s*[^\n]+', '', desc)
    desc = re.sub(r'\nGenre:\s*[^\n]+', '', desc)
    # Remove extra blank lines
    desc = re.sub(r'\n{3,}', '\n\n', desc)
    return desc.strip()

def card_has_comments(card_id):
    url = f"https://api.trello.com/1/cards/{card_id}/actions"
    query = {'key': TRELLO_KEY, 'token': TRELLO_TOKEN, 'filter': 'commentCard'}
    response = requests.get(url, params=query)
    actions = response.json()
    return len(actions) > 0

def find_movie_title_tmdb(query):
    url = "https://api.themoviedb.org/3/search/movie"
    params = {
        'api_key': TMDB_API_KEY,
        'query': query,
        'include_adult': False,
        'language': 'en-US',
        'page': 1
    }
    response = requests.get(url, params=params)
    print(response.text)
    data = response.json()
    results = data.get('results', [])
    if not results:
        return None
    # Return the first result's title and year
    movie = results[0]
    title = movie.get('title')
    rating = movie.get('vote_average')
    genreids = movie.get('genre_ids')
    gmapping = get_tmdb_genre_mapping()
    genres = [gmapping.get(gid) for gid in genreids if gmapping.get(gid)]
    year = movie.get('release_date', '')[:4] if movie.get('release_date') else ''
    return {
        'episodes': "CANT",
        'imdb_rating': rating,
        'genre': genres
    }

def get_episode_count_from_desc(desc):
    match = re.search(r"Episodes:\s*(\d+)", desc)
    if match:
        return int(match.group(1))
    return None

def get_plex_episode_count(title):
    plex = PlexServer(PLEX_URL, PLEX_TOKEN)
    shows = plex.library.section('TV Shows').search(title=title)
    if shows:
        show = shows[0]
        return sum(len(season.episodes()) for season in show.seasons())
    return 0

def get_plex_completion_percentage(card):
    desc = card.get('desc', '') or ''
    expected_episodes = get_episode_count_from_desc(desc)
    if not expected_episodes:
        return None  # Can't determine percentage without expected count
    plex_episodes = get_plex_episode_count(card['name'])
    if expected_episodes == 0:
        return None
    percent = (plex_episodes / expected_episodes) * 100
    return round(percent, 2)

def get_cards_in_list(list_id):
    url = f"https://api.trello.com/1/lists/{list_id}/cards"
    query = {'key': TRELLO_KEY, 'token': TRELLO_TOKEN}
    response = requests.get(url, params=query)
    return response.json()

def get_label_id(board_id, label_name):
    url = f"https://api.trello.com/1/boards/{board_id}/labels"
    query = {'key': TRELLO_KEY, 'token': TRELLO_TOKEN}
    response = requests.get(url, params=query)
    labels = response.json()
    for label in labels:
        if label['name'].lower() == label_name.lower():
            return label['id']
    return None

def fetch_tmdb_top_100_movies():
    url = "https://api.themoviedb.org/3/movie/top_rated"
    params = {
        "api_key": TMDB_API_KEY,
        "language": "en-US",
        "page": 1
    }
    movies = []
    for page in range(1, 6):  # 20 results per page, 5 pages = 100
        params["page"] = page
        response = requests.get(url, params=params)
        data = response.json()
        movies.extend([m["title"] for m in data.get("results", [])])
    return movies[:100]

def fetch_tmdb_top_100_tv():
    url = "https://api.themoviedb.org/3/tv/top_rated"
    params = {
        "api_key": TMDB_API_KEY,
        "language": "en-US",
        "page": 1
    }
    shows = []
    for page in range(1, 6):
        params["page"] = page
        response = requests.get(url, params=params)
        data = response.json()
        shows.extend([s["name"] for s in data.get("results", [])])
    return shows[:100]

def get_imdb_info(title):
    url = f"http://www.omdbapi.com/"
    params = {'t': title, 'apikey': OMDB_API_KEY}
    response = requests.get(url, params=params)
    data = response.json()
    if data.get('Error') == 'Request limit reached!':
        raise RuntimeError("OMDb API limit reached")
    if data is None or data.get('imdbRating') == 'N/A' or data.get('Error') == "Movie not found!" or data.get('imdbRating') is None:
        # Try TMDb as a fallback
        tmdb_title = find_movie_title_tmdb(title)
        if tmdb_title is not None:
            return tmdb_title

    return {
        'episodes': data.get('totalSeasons') if data.get('Type') == 'series' else None,
        'imdb_rating': data.get('imdbRating'),
        'genre': data.get('Genre')
    }

def get_tmdb_genre_mapping():
    url = "https://api.themoviedb.org/3/genre/movie/list"
    params = {'api_key': TMDB_API_KEY, 'language': 'en-US'}
    response = requests.get(url, params=params)
    genres = response.json().get('genres', [])
    # Returns a dict: {id: name}
    return {genre['id']: genre['name'] for genre in genres}

def get_total_episodes(title):
    # Get total seasons first
    url = "http://www.omdbapi.com/"
    params = {'t': title, 'apikey': OMDB_API_KEY}
    response = requests.get(url, params=params)
    data = response.json()
    if data.get('Error') == 'Request limit reached!':
        raise RuntimeError("OMDb API limit reached")
    if data.get('Type') != 'series' or not data.get('totalSeasons') or not data.get('totalSeasons').isdigit():
        return None
    total_seasons = int(data['totalSeasons'])
    total_episodes = 0
    for season in range(1, total_seasons + 1):
        params = {'t': title, 'Season': season, 'apikey': OMDB_API_KEY}
        resp = requests.get(url, params=params)
        season_data = resp.json()
        if season_data.get('Error') == 'Request limit reached!':
            raise RuntimeError("OMDb API limit reached")
        if 'Episodes' in season_data:
            total_episodes += len(season_data['Episodes'])
    return total_episodes

def get_custom_fields(board_id):
    url = f"https://api.trello.com/1/boards/{board_id}/customFields"
    query = {'key': TRELLO_KEY, 'token': TRELLO_TOKEN}
    response = requests.get(url, params=query)
    return response.json()

def get_progress_field_id(board_id):
    fields = get_custom_fields(board_id)
    for field in fields:
        if field['name'].lower() == 'progress':
            return field['id']
    return None

def set_card_progress(card_id, field_id, percent):
    url = f"https://api.trello.com/1/card/{card_id}/customField/{field_id}/item"
    query = {'key': TRELLO_KEY, 'token': TRELLO_TOKEN}
    data = {
        "value": {
            "number": str(percent)
        }
    }
    requests.put(url, params=query, json=data)


def add_card_to_list(list_id, title, desc=''):
    existing_cards = get_cards_in_list(list_id)
    if any(card['name'].strip().lower() == title.strip().lower() for card in existing_cards):
        print(f"Card '{title}' already exists in list ID: {list_id}. Skipping.")
        return
    url = f"https://api.trello.com/1/cards"
    query = {
        'key': TRELLO_KEY,
        'token': TRELLO_TOKEN,
        'idList': list_id,
        'name': title,
        'desc': desc
    }
    requests.post(url, params=query)
    print(f"Added card: {title} to list ID: {list_id}")


def process_imdb_top_100():
    # Fetch titles
    top_movies = fetch_tmdb_top_100_movies()
    top_series = fetch_tmdb_top_100_tv()
    # Process movies
    for title in top_movies:
        #result = check_plex_title(title)
        #if not result.get('found'):
        add_card_to_list(MOVIE_LIST_ID, title, desc="IMDb Top 100 Movie")

    # Process series
    for title in top_series:
        #result = check_plex_title(title)
        #if not result.get('found') or (result.get('type') == 'series' and not result.get('matches')):
        add_card_to_list(TV_LIST_ID, title, desc="IMDb Top 100 Series (Not fully watched in Plex)")


def search_trailer(movie_title):
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    try:
        request = youtube.search().list(
            q=f"{movie_title} official trailer",
            part='snippet',
            maxResults=1,
            type='video'
        )
        response = request.execute()
        if response['items']:
            video_id = response['items'][0]['id']['videoId']
            return f"https://www.youtube.com/watch?v={video_id}", 200
        return None, 200
    except HttpError as e:
        if e.resp.status == 403:
            print("Received 403 Forbidden from YouTube API. Stopping.")
            return None, 403
        return None, e.resp.status

def update_episode_count_in_desc(desc, correct_count):
    # Replace or add the Episodes line
    if "Episodes:" in desc:
        desc = re.sub(r"Episodes:\s*\d+", f"Episodes: {correct_count}", desc)
    else:
        desc += f"\nEpisodes: {correct_count}"
    return desc

def update_card_description(card_id, new_desc):
    url = f"https://api.trello.com/1/cards/{card_id}/desc"
    query = {'key': TRELLO_KEY, 'token': TRELLO_TOKEN, 'value': new_desc}
    requests.put(url, params=query)

def scan_shows_daily():
    for list_id in SHOWS:
        cards = get_cards_in_list(list_id)
        for card in cards:
            percent = get_plex_completion_percentage(card)
            board_id = card['idBoard']
            progress_field_id = get_progress_field_id(board_id)
            if progress_field_id and percent is not None:
                set_card_progress(card['id'], progress_field_id, percent)
                print(f"Set progress for {card['name']}: {percent}%")
            else:
                print(f"Could not set progress for {card['name']}")
            time.sleep(0.5)


# --- MAIN EXECUTION ---
if __name__ == "__main__":
    LISTS_TRELLO = [TV_LIST_ID, MOVIE_LIST_ID]  # You can add more list IDs here if needed
    limit = 100
    process_imdb_top_100()
    for backlog in LISTS_TRELLO:
        cards = get_cards_in_list(backlog)
        for card in cards:
            if limit <= 0:
                print("Reached processing limit. Stopping.")
                break
            desc = card.get('desc', '') or ''
            if "IMDb Rating" in desc:
                continue
            movie_name = card['name']
            print(f"Processing: {movie_name}")

                # Get IMDb info and append to description
            imdb_info = get_imdb_info(movie_name)
            desc = clean_description(desc)
            print(imdb_info)
            details = f"\n\nIMDb Info:\n"
            if imdb_info['episodes']:
                correct_count = get_total_episodes(movie_name)
                print(correct_count)
                if correct_count:
                    details += f"Episodes: {correct_count}\n"
            if imdb_info['imdb_rating']:
                details += f"IMDb Rating: {imdb_info['imdb_rating']}\n"
            if imdb_info['genre']:
                details += f"Genre: {imdb_info['genre']}\n"
            new_desc = desc + details
            update_card_description(card['id'], new_desc)
            time.sleep(0.5)
            if card_has_comments(card['id']) or card.get('desc', '').strip():
                continue
            if backlog == TV_LIST_ID:
                movie_name = movie_name + " season 1"
            trailer_url, yt_status = search_trailer(movie_name)
            if yt_status == 403:
                break
            limit -= 1
            if trailer_url:
                add_comment_to_card(card['id'], f"Trailer: {trailer_url}")
                print(f"Added trailer for {movie_name}")
            else:
                print(f"Trailer not found for {movie_name}")