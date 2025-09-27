SELECT source_sensor, COUNT(*) as count 
FROM koi_memories 
WHERE superseded_at IS NULL 
  AND event_type != 'FORGET' 
  AND published_at >= NOW() - INTERVAL '48 hours' 
  AND published_confidence >= 0.5 
  AND source_sensor IN (
    SELECT DISTINCT source_sensor 
    FROM koi_memories 
    WHERE source_sensor LIKE '%discourse%' 
      OR source_sensor LIKE '%github%' 
      OR source_sensor LIKE '%gitlab%' 
      OR source_sensor LIKE '%medium%' 
      OR source_sensor LIKE '%website%'
  ) 
  AND content::text NOT LIKE '%sensor_heartbeat%' 
  AND rid NOT LIKE '%heartbeat%' 
GROUP BY source_sensor 
ORDER BY count DESC;
