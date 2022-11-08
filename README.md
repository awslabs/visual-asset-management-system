```             ____               
               ,---,               ,'  , `.  .--.--.    
       ,---.  '  .' \           ,-+-,.' _ | /  /    '.  
      /__./| /  ;    '.      ,-+-. ;   , |||  :  /`. /  Very
 ,---.;  ; |:  :       \    ,--.'|'   |  ;|;  |  |--`     Thorough
/___/ \  | |:  |   /\   \  |   |  ,', |  ':|  :  ;_          Setup
\   ;  \ ' ||  :  ' ;.   : |   | /  | |  || \  \    `.        & 
 \   \  \: ||  |  ;/  \   \'   | :  | :  |,  `----.   \      Deployment
  ;   \  ' .'  :  | \  \ ,';   . |  ; |--'   __ \  \  |    Guide
   \   \   '|  |  '  '--'  |   : |  | ,     /  /`--'  / (WIP)
    \   `  ;|  :  :        |   : '  |/     '--'.     /  
     :   \ ||  | ,'        ;   | |`-'        `--'---'   
      '---" `--''          |   ;/                       
                           '---'                            
```

## Security Warning
VAMS is provided under a shared responsibility model. Any customization for customer use must go through an AppSec review to confirm the modifications don't introduce new vulnerabilities. Any team implementing takes on the responsibility of ensuring their implementation has gone through a proper security review.

1) Run `npm audit` in the `web` directory prior to deploying front-end to ensure all packages are up-to-date. Run `npm audit fix` to mitigate critical security vulnerabilities.
2) When deploying to a customer account, create an IAM Role for deployment that limits access to the least privilege necessary based on the customers internal security policies.

## Requirements

* Python 3.8
* Node 16.x
* Node Version Manager (nvm)
* AWS cli
* CDK cli
* Amplify cli
* Programatic access to AWS account at minimum access levels outlined above.

## Build & Deploy Steps

1) `cd ./web nvm use` - make sure you're node version matches the project. 

   Success: Console outputs `Now using node v16.11.0 (npm v8.0.0)`

   Failure Scenario 1: nvm not installed, visit [nvm.sh](nvm.sh)

   Failure Scenario 2: Console output `You need to run "nvm install v16.11.0" to install it before using it.`, follow instructions.

2) `npm run build` - builds the web app

   Success: Console output includes `The build folder is ready to be deployed.`

   Failure Scenario 1: npm not installed, visit [nodejs.org](nodejs.org)

   Failure Scenario 2: npm install fails with console error, visit [https://docs.npmjs.com/common-errors](https://docs.npmjs.com/common-errors)

3) `cd ../infra npm install` - installs dependencies in package.json

   Success: Console output includes `added # packages, and audited # packages in #s` without error message outlined here: [https://docs.npmjs.com/common-errors](https://docs.npmjs.com/common-errors)

   Failure Scenario 1: npm not installed, visit [nodejs.org](nodejs.org)

   Failure Scenario 2: npm install fails with error message, visit [https://docs.npmjs.com/common-errors](https://docs.npmjs.com/common-errors)

4) `cdk bootstrap aws://101010101010/us-east-1` - replace with your account and region

   Success: Console output includes `environment aws://101010101010/us-east-1 bootstrapped`

   Failure Scenario 1: No AWS credential set, use `$(isengardcli creds)` or `aws configure` to provide AWS account keys.

   Failure Scenario 2: Console error output includes `Error: Not downgrading existing bootstrap stack from version`, this account and region is already bootstrapped.

5) `cdk deploy dev --parameters adminEmailAddress=myuser@amazon.com` - replace with your email address to deploy dev stack
6) `cdk deploy prod --parameters adminEmailAddress=myuser@amazon.com` - replace with your email address to deploy prod stack
   
   Success: Console output includes `Outputs:` with your deployment resource ids.

   Failure Scenario 1: Console error output includes `NoSuchBucket error`, you must first run `cdk bootstrap...`

   Failure Scenario 2: Console error output includes `forbidden: null`, your console IAM role lacks permissions to write to deployment s3 bucket. Console IAM role must include `s3:*` permissions for bootstrap bucket.

## Deployment Success

1) Navigate to URL provided in `{stackName].WebAppCloudFrontDistributionDomainName{uuid}` from `cdk deploy` output.

2) Check email for temporary account password.

## Other Fail Scenarios

1) If output website loads as blank white screen, try re-running `npm run build` in web folder, then replace the `{stackName}webapp{uuid}` s3 bucket contents with `build` folder contents. Invalidate CloudFront distribution with `/*` pattern from CLI or AWS console.

## Multiple Deployments Same Account/Region

Providing a unique stack name in the deployment command `cdk deploy STACK_NAME --parameters adminEmailAddress=myuser@amazon.com` will allow for this to work without conflicts.

## Quick Deploy Internal AWS

`isengardcli assume`

`$(isengardcli creds)`

`npm run deploy.dev -- --parameters adminEmailAddress=myuser@amazon.com`

