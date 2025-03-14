import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import text, create_engine
from datetime import datetime

# App title and configuration
st.set_page_config(page_title="Chuj", layout="wide")

# Database connection
conn = st.connection('game_db', type='sql')

# Initialize database if needed
def initialize_database():
    with conn.session as s:
        # Create params table if it doesn't exist
        s.execute(text('''
            CREATE TABLE IF NOT EXISTS params (
                name TEXT PRIMARY KEY,
                value TEXT
            );
        '''))
        
        # Create games_history table if it doesn't exist
        s.execute(text('''
            CREATE TABLE IF NOT EXISTS games_history (
                game_id INTEGER,
                player TEXT,
                score INTEGER,
                round INTEGER,
                timestamp TEXT
            );
        '''))

        # Create chuj_history table if it doesn't exist
        s.execute(text('''
            CREATE TABLE IF NOT EXISTS chuj_history (
                game_id INTEGER,
                player TEXT,
                score INTEGER         
            );            
        '''))
        
        # Initialize state parameter if it doesn't exist
        result = s.execute(text("SELECT COUNT(*) FROM params WHERE name = 'state';")).fetchone()
        if result[0] == 0:
            s.execute(text("INSERT INTO params (name, value) VALUES ('state', 'no_current_game');"))
        
        s.commit()

# Initialize database
initialize_database()

# Get current application state
def get_app_state():
    result = conn.query("SELECT value FROM params WHERE name = 'state';", ttl=0)
    return result["value"][0] if not result.empty else "no_current_game"

# Available players
PLAYER_OPTIONS = ['Mata', 'Fabi', 'Rasto', 'Mato', 'Janka']
WINNING_SCORE = 100

# Main application logic
def main():
    state = get_app_state()
    
    # Create tabs for different app sections
    tab1, tab2 = st.tabs(["Hra", "Štatistiky"])
    
    with tab1:
        if state == 'no_current_game':
            display_new_game_screen()
        elif state == 'game_created':
            display_current_game()
    
    with tab2:
        display_game_history()

def display_new_game_screen():
    st.title("Nová hra")
    
    # Number of players selection
    number_of_players = st.number_input(
        'Počet hráčov novej hry', 
        min_value=3, 
        max_value=4, 
        value=4
    )
    
    # Player selection
    players = st.multiselect(
        'Výber hráčov novej hry (na poradí záleží)', 
        options=PLAYER_OPTIONS, 
        max_selections=number_of_players,
        key="players"
    )
    
    # Start new game button
    if st.button("Nová hra", key="new_game_button"):
        if len(players) != number_of_players:
            st.error('Bol vybratý zlý počet hráčov', icon="🚨")
        else:
            start_new_game(players)
            st.success('Nová hra bola vytvorená!', icon="✅")
            st.rerun()

def start_new_game(players):
    with conn.session as s:
        # Update app state
        s.execute(text("UPDATE params SET value = 'game_created' WHERE name = 'state';"))
        
        # Create and initialize current game table
        s.execute(text("CREATE TABLE IF NOT EXISTS current_game (player TEXT, score INTEGER, round INTEGER);"))
        s.execute(text("DELETE FROM current_game;"))
        
        for player in players:
            s.execute(
                text("INSERT INTO current_game (player, score, round) VALUES (:player, :score, :round);"), 
                params=dict(player=player, score=0, round=0)
            )
        
        s.commit()

def display_current_game():
    # Get current game data
    current_round = int(conn.query("SELECT MAX(round) AS round FROM current_game;", ttl=0)['round'][0])
    players = conn.query("SELECT player FROM current_game WHERE round = 0;", ttl=0)['player']
    current_data = conn.query("SELECT * FROM current_game;", ttl=0)
    
    # Display game title and dealer information
    st.title("Aktuálna hra")
    st.subheader(f"Kolo {current_round} - rozdáva {players[current_round % len(players)]}")
    
    # Display game scores
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        # Create pivot table for scores
        data = current_data.pivot_table(
            index='round', 
            columns='player', 
            values='score',
            aggfunc='sum'
        ).reindex(players, axis=1)
        
        # Add running total row
        cumulative_data = data.fillna(0).cumsum()
        
        # Display scores table
        
        st.subheader("Celkové skóre")
        st.dataframe(cumulative_data.astype(int), use_container_width=True)

        st.subheader("Skóre po kolách")
        st.dataframe(data.fillna(0).astype(int), use_container_width=True)
    
    with col2:
        # Input scores for current round
        st.subheader("Zadanie skóra kola")
        round_scores = {}
        total_score = 0
        
        for player in players:
            score = st.number_input(
                f"{player}", 
                min_value=0, 
                value=0,
                key=f"score_{player}"
            )
            round_scores[player] = score
            total_score += score
        
        st.caption(f"Celkové skóre kola: {total_score}")
        
        # Submit round button
        if st.button("Zapíš kolo"):
            submit_round(players, round_scores, current_round + 1)

            

    with col3:
        # Bodky
        st.subheader("Bodky")
        st.write(" ")
        for player in players:
            # Initialize session state for the checkbox
            key = f"{player}bodka"
            if key not in st.session_state:
                st.session_state[key] = False
            
            bodka = st.checkbox(f"{player}", key=key)

            if bodka:
                st.write(f"{player} má bodku!")
                st.write(" ")
            else:
                st.write(" ")
                st.write(" ")
                st.write(" ")
            
    
    # Reset game button
    if st.button("Koniec hry"):
        end_current_game_without_saving()
        st.rerun()

def submit_round(players, round_scores, new_round):
    
    with conn.session as s:
        # Insert round scores
        for player in players:
            s.execute(
                text("INSERT INTO current_game (player, score, round) VALUES (:player, :score, :round)"), 
                params=dict(player=player, score=round_scores[player], round=new_round)
            )
        s.commit()
    for player in players:
        key = f"{player}bodka"
        st.session_state[key] = False

    # Check if the game should end AFTER submitting scores
    check_game_end()

def check_game_end():
    # Query to get summed scores per player
    final_scores = conn.query("SELECT player, SUM(score) as score FROM current_game GROUP BY player;", ttl=0)

    winner = None
    winning_score = 0

    # Make sure we have data
    if not final_scores.empty:
        for _, row in final_scores.iterrows():
            # Ensure we're comparing numbers
            player_score = float(row['score'])
            if player_score >= WINNING_SCORE and player_score > winning_score:
                winner = row['player']
                winning_score = player_score

        if winner is not None:
            st.balloons()
            st.success(f"Koniec hry! {winner} je víťaz so skóre {int(winning_score)}!")
            
            # End the game properly
            end_current_game()
        else:
            st.rerun()  

def end_current_game_without_saving():
    with conn.session as s:
        s.execute(text("UPDATE params SET value = 'no_current_game' WHERE name = 'state';"))
        s.execute(text("DROP TABLE IF EXISTS current_game;"))
        s.commit()

def end_current_game():
    # First save the game to history
    save_game_to_history()
    
    # Then update the state and drop the table
    with conn.session as s:
        s.execute(text("UPDATE params SET value = 'no_current_game' WHERE name = 'state';"))
        s.execute(text("DROP TABLE IF EXISTS current_game;"))
        s.commit()

def save_game_to_history():
    # Get current game data
    game_data = conn.query("SELECT * FROM current_game;", ttl=0)
    
    # Get new game ID
    game_id_result = conn.query('''
        SELECT COALESCE(MAX(game_id), 0) + 1 AS new_id 
        FROM games_history;
    ''', ttl=0)
    
    game_id = int(game_id_result['new_id'][0])
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Save to history
    with conn.session as s:
        for _, row in game_data.iterrows():
            s.execute(text('''
                INSERT INTO games_history (game_id, player, score, round, timestamp)
                VALUES (:game_id, :player, :score, :round, :timestamp);
            '''), params=dict(
                game_id=game_id,
                player=row['player'],
                score=row['score'],
                round=row['round'],
                timestamp=timestamp
            ))
        s.commit()

def get_chuj_stats():
    # Query the database for chuj statistics
    chuj_data = conn.query('''
        SELECT player, COUNT(*) as chuj_count FROM (SELECT game_id, player, SUM(score) as total_score
                                        FROM games_history
                                        GROUP BY game_id, player
                                        HAVING SUM(score) > 100) a GROUP BY player;
    ''', ttl=0)
    
    if chuj_data.empty:
        # If no data exists, create a dataframe with 0 counts for all players
        chuj_data = pd.DataFrame({'player': PLAYER_OPTIONS, 'chuj_count': [0] * len(PLAYER_OPTIONS)})
    
    return chuj_data

def display_game_history():
    st.title("História")
    
    # Display chuj statistics chart
    chuj_stats = get_chuj_stats()
    
    if not chuj_stats.empty:
        fig = px.bar(
            chuj_stats, 
            x='player', 
            y='chuj_count',
            title="Koľko krát bol kto Chuj",
            labels={'player': 'Hráč', 'chuj_count': 'Absolútny počet'},
            color='chuj_count',
            color_continuous_scale='Viridis'
        )
        fig.update_layout(xaxis_title="Hráč", yaxis_title="Počet")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Žiadne štatistiky.")
    
    # Get list of all games
    games_list = conn.query('''
        SELECT DISTINCT game_id, timestamp 
        FROM games_history 
        ORDER BY game_id DESC;
    ''', ttl=0)
    
    if games_list.empty:
        st.info("Žiadna história!")
        return
    
    # Display game selection
    selected_game = st.selectbox(
        "Vyber minulú hru",
        options=games_list['game_id'],
        format_func=lambda x: f"Hra #{x} - {games_list.loc[games_list['game_id'] == x, 'timestamp'].iloc[0]}"
    )
    
    if selected_game:
        # Get data for selected game
        game_data = conn.query(f'''
            SELECT * FROM games_history 
            WHERE game_id = {selected_game}
            ORDER BY round, player;
        ''', ttl=0)
        
        # Get chuj data for this game
        game_chuj_data = conn.query(f'''
            SELECT player, COUNT(*) as chuj_count 
            FROM chuj_history 
            WHERE game_id = {selected_game}
            GROUP BY player;
        ''', ttl=0)
        
        # Get players in this game
        players = game_data[game_data['round'] == 0]['player'].unique()
        
        # Create pivot table for round scores
        pivot_data = game_data.pivot_table(
            index='round', 
            columns='player', 
            values='score',
            aggfunc='sum'
        )
        
        # Calculate running totals
        cumulative_data = pivot_data.fillna(0).cumsum()
        
        # Get game winner
        winner_data = cumulative_data.iloc[-1].idxmax()
        winner_score = cumulative_data.iloc[-1].max()
        
        # Display game information
        st.subheader(f"Hra #{selected_game} - {games_list.loc[games_list['game_id'] == selected_game, 'timestamp'].iloc[0]}")
        st.info(f"Víťaz: {winner_data} so skóre {int(winner_score)}")
        
        # Display 'chuj' info for this game if available
        if not game_chuj_data.empty:
            st.caption(f"'Chuj' counts for this game: {', '.join([f'{row.player}: {int(row.chuj_count)}' for _, row in game_chuj_data.iterrows()])}")
        
        st.subheader("Celkové skóre")
        st.dataframe(cumulative_data.astype(int), use_container_width=True)

# Run the app
if __name__ == "__main__":
    main()
