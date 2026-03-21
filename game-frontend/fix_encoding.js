const fs = require('fs');
const content = fs.readFileSync('C:\\Users\\zhang\\Desktop\\DN\\game-frontend\\style.css', 'utf8');
fs.writeFileSync('C:\\Users\\zhang\\Desktop\\DN\\game-frontend\\style.css', content, 'utf8');
console.log('done');
