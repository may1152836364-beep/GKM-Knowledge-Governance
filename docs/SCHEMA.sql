-- Schema v2. Upgrades backed up automatically by Store.

CREATE TABLE IF NOT EXISTS articles(id INTEGER PRIMARY KEY, article_id TEXT NOT NULL, language TEXT NOT NULL, source_scope TEXT NOT NULL, current_version INTEGER, category TEXT, release_status TEXT, UNIQUE(article_id,language,source_scope));
CREATE TABLE IF NOT EXISTS article_versions(id INTEGER PRIMARY KEY, article_fk INTEGER NOT NULL REFERENCES articles(id), title TEXT NOT NULL, raw_content TEXT NOT NULL, clean_content TEXT NOT NULL, content_hash TEXT NOT NULL, metadata_json TEXT NOT NULL, parse_status TEXT NOT NULL, clean_version TEXT NOT NULL DEFAULT 'preserve-v1', source_updated_at TEXT, import_batch_id TEXT, source_file TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS av_article ON article_versions(article_fk);
CREATE INDEX IF NOT EXISTS av_batch ON article_versions(import_batch_id);
CREATE INDEX IF NOT EXISTS article_filter ON articles(language,release_status,category);
CREATE TABLE IF NOT EXISTS import_batches(id TEXT PRIMARY KEY, created_at REAL, report TEXT, mapping TEXT);
CREATE TABLE IF NOT EXISTS model_profiles(id TEXT PRIMARY KEY, name TEXT, base_url TEXT, model TEXT, key_ref TEXT, options TEXT, tested_hash TEXT, test_report TEXT);
CREATE TABLE IF NOT EXISTS prompt_versions(id INTEGER PRIMARY KEY, created_at REAL, system TEXT, business TEXT, output TEXT, rules TEXT, note TEXT, published INTEGER DEFAULT 0, case_prompt TEXT NOT NULL DEFAULT '参考案例仅用于相同事实和适用边界；不可把案例当作当前文章外部事实');
CREATE TABLE IF NOT EXISTS analysis_jobs(id TEXT PRIMARY KEY, created_at REAL, state TEXT, snapshot TEXT, budget REAL, spent REAL DEFAULT 0, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, requests INTEGER DEFAULT 0, error TEXT);
CREATE TABLE IF NOT EXISTS job_items(id INTEGER PRIMARY KEY, job_fk TEXT NOT NULL REFERENCES analysis_jobs(id), article_version_fk INTEGER NOT NULL REFERENCES article_versions(id), state TEXT NOT NULL, attempts INTEGER DEFAULT 0, retry_at REAL DEFAULT 0, error TEXT, UNIQUE(job_fk,article_version_fk));
CREATE INDEX IF NOT EXISTS item_queue ON job_items(job_fk,state,retry_at,id);
CREATE INDEX IF NOT EXISTS item_pending ON job_items(job_fk,state,id);
CREATE TABLE IF NOT EXISTS analysis_results(id INTEGER PRIMARY KEY, job_item_fk INTEGER UNIQUE REFERENCES job_items(id), result TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS call_logs(id INTEGER PRIMARY KEY, job_fk TEXT, item_fk INTEGER, kind TEXT, input_tokens INTEGER, output_tokens INTEGER, cost REAL, created_at REAL);
CREATE TABLE IF NOT EXISTS chunk_cache(item_fk INTEGER, chunk_hash TEXT, result TEXT, PRIMARY KEY(item_fk,chunk_hash));
CREATE TABLE IF NOT EXISTS case_usage(result_fk INTEGER NOT NULL REFERENCES analysis_results(id),case_id TEXT NOT NULL,PRIMARY KEY(result_fk,case_id));
CREATE INDEX IF NOT EXISTS case_usage_lookup ON case_usage(case_id);
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, result_fk INTEGER REFERENCES analysis_results(id), recommendation TEXT, reason TEXT, reviewer TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY, version INTEGER, content TEXT, status TEXT, enabled INTEGER, created_at REAL);
CREATE TABLE IF NOT EXISTS case_history(case_id TEXT,version INTEGER,content TEXT,status TEXT,created_at REAL,PRIMARY KEY(case_id,version));
CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY, created_at REAL, action TEXT, object_id TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY,value TEXT);

PRAGMA user_version=2;
