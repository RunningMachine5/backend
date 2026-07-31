-- 컨테이너 최초 기동 시 DB 에 pgvector/pg_search 확장을 생성
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_search;