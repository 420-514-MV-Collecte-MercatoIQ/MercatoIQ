// Initialisation de la base MongoDB MercatoIQ (Architecture Médaillon : Bronze -> Silver -> Gold)
const dbName = process.env.MONGO_INITDB_DATABASE || 'mercatoiq';
const dbRef = db.getSiblingDB(dbName);

// --- 1. Couche BRONZE : Index uniques sur les IDs sources (>100k observations sans doublons) ---
dbRef.createCollection('bronze_appearances');
dbRef.bronze_appearances.createIndex({ appearance_id: 1 }, { unique: true });

dbRef.createCollection('bronze_game_events');
dbRef.bronze_game_events.createIndex({ game_event_id: 1 }, { unique: true });

dbRef.createCollection('bronze_transfers');
dbRef.bronze_transfers.createIndex({ transfer_id: 1 }, { unique: true });

dbRef.createCollection('bronze_players');
dbRef.bronze_players.createIndex({ player_id: 1 }, { unique: true });

dbRef.createCollection('bronze_clubs');
dbRef.bronze_clubs.createIndex({ club_id: 1 }, { unique: true });

dbRef.createCollection('bronze_games');
dbRef.bronze_games.createIndex({ game_id: 1 }, { unique: true });

dbRef.createCollection('bronze_player_valuations');
dbRef.bronze_player_valuations.createIndex({ player_id: 1, date: 1 }, { unique: true });

// --- 2. Couche SILVER : Index composés pour le filtrage et les jointures ---
dbRef.createCollection('silver_players');
dbRef.silver_players.createIndex({ player_id: 1 }, { unique: true });
dbRef.silver_players.createIndex({ position: 1, 'current_club.competition_id': 1 });

dbRef.createCollection('silver_appearances');
dbRef.silver_appearances.createIndex({ appearance_id: 1 }, { unique: true });
dbRef.silver_appearances.createIndex({ competition_id: 1, season: 1, player_id: 1 });

dbRef.createCollection('silver_transfers');
dbRef.silver_transfers.createIndex({ transfer_id: 1 }, { unique: true });
dbRef.silver_transfers.createIndex({ season: 1, to_club_id: 1, player_id: 1 });

// --- 3. Couche GOLD & Opérationnel ---
dbRef.createCollection('gold_club_transfer_roi');
dbRef.createCollection('gold_undervalued_scouting');
dbRef.createCollection('gold_ucl_vs_domestic');
dbRef.createCollection('gold_super_subs');

dbRef.createCollection('pipeline_logs');
dbRef.pipeline_logs.createIndex({ started_at: -1 });
dbRef.pipeline_logs.createIndex({ status: 1, trigger_type: 1 });

print(`[MercatoIQ] Base '${dbName}' et index Bronze/Silver/Gold initialises avec succes.`);
