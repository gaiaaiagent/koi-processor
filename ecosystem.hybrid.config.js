module.exports = {
  apps: [{
    name: 'hybrid-rag-api',
    script: 'koi-query-api.ts',
    interpreter: '/home/darren/.bun/bin/bun',
    cwd: '/opt/projects/koi-processor',
    env: {
      POSTGRES_URL: 'postgresql://postgres:postgres@localhost:5433/eliza',
      DEBUG_GRAPH_EXPANSION: 'false',
      ENABLE_POLYSEMY_RERANK: 'true',
      DEBUG_POLYSEMY_RERANK: 'false'
    },
    out_file: 'logs/pm2-hybrid-rag.log',
    error_file: 'logs/pm2-hybrid-rag-error.log'
  }]
};
