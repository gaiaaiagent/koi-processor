#!/bin/bash

# Apply all migrations in order
export PGPASSWORD=postgres

echo "Applying remaining migrations..."

# Apply migration 003
echo "Applying 003_create_isolated_koi_tables..."
psql -h localhost -p 5433 -U postgres -d eliza -f migrations/003_create_isolated_koi_tables.sql 2>&1 | grep -E "ERROR|WARNING" || true
psql -h localhost -p 5433 -U postgres -d eliza -c "INSERT INTO schema_migrations (version) VALUES ('003_create_isolated_koi_tables') ON CONFLICT DO NOTHING;"

# Apply migration 004
echo "Applying 004_add_publication_dates..."
psql -h localhost -p 5433 -U postgres -d eliza -f migrations/004_add_publication_dates.sql 2>&1 | grep -E "ERROR|WARNING" || true
psql -h localhost -p 5433 -U postgres -d eliza -c "INSERT INTO schema_migrations (version) VALUES ('004_add_publication_dates') ON CONFLICT DO NOTHING;"

# Apply migration 005 (dashboard tables)
echo "Applying 005_create_dashboard_tables..."
psql -h localhost -p 5433 -U postgres -d eliza -f migrations/005_create_dashboard_tables.sql 2>&1 | grep -E "ERROR|WARNING" || true
psql -h localhost -p 5433 -U postgres -d eliza -c "INSERT INTO schema_migrations (version) VALUES ('005_create_dashboard_tables') ON CONFLICT DO NOTHING;"

# Apply migration 006
echo "Applying 006_fix_cat_receipts..."
psql -h localhost -p 5433 -U postgres -d eliza -f migrations/006_fix_cat_receipts.sql 2>&1 | grep -E "ERROR|WARNING" || true
psql -h localhost -p 5433 -U postgres -d eliza -c "INSERT INTO schema_migrations (version) VALUES ('006_fix_cat_receipts') ON CONFLICT DO NOTHING;"

# Apply migration 007 (improved storage)
echo "Applying 007_improved_storage_architecture..."
psql -h localhost -p 5433 -U postgres -d eliza -f migrations/007_improved_storage_architecture.sql 2>&1 | grep -E "ERROR|WARNING" || true
psql -h localhost -p 5433 -U postgres -d eliza -c "INSERT INTO schema_migrations (version) VALUES ('007_improved_storage_architecture') ON CONFLICT DO NOTHING;"

echo "All migrations applied!"

# Show status
echo ""
echo "Migration status:"
psql -h localhost -p 5433 -U postgres -d eliza -c "SELECT version, applied_at FROM schema_migrations ORDER BY applied_at;"