function xmlToJson(xml) {
    let obj = {};
    // const responseContent = xml.match(/<response>(.*)<\/response>/s)[1];
    let tags = xml.match(/<(.*?)>/g);
    console.log(tags)
    const mainKeys = ['<response>', '<observation>', '<mindspace>', '<thought>', '<type>', '<result>', '<tool_calls>', '<artifacts>'];
    let isSubLevel = false
    if (tags) {
        tags.forEach(tag => {
            let tagName = tag.match(/<(.*)>/)[1];
            if (!tagName.startsWith('/')) {
                let regex = xml.match(new RegExp(`<${tagName}>(.*?)<\/${tagName}>`, 's'));
                console.log(tagName)
                let content = regex[1];
                // if (mainKeys.includes(tag) && !isSubLevel) {
                //     isSubLevel = false
                // }
                // else {
                //     isSubLevel = true
                // }

                if (content.includes('<')) {
                    obj[tagName] = xmlToJson(content);
                } else {
                    obj[tagName] = content.trim();
                }
            }
        });
    }

    return obj;
}

// Test the function
let xml = `
<response>
    <observation>The user asked for a Python script to calculate the factorial of a number.</observation>
    <mindspace>
Mathematical: Factorial operation, recursive function
Programming: Python syntax, function definition, conditional statements
Educational: Explaining the concept of factorial
Practical: Use cases for factorial calculations
    </mindspace>
    <thought>Step 1) A factorial calculation can be implemented using a recursive function in Python.
Step 2) We should create a function that handles both the base case and the recursive case.
Step 3) Let's create an artifact with the Python script for calculating factorials.</thought>
    <type>final_answer</type>
    <result>I've created a Python script that calculates the factorial of a given number using a recursive function. You can find the script in the artifact below.</result>
    <artifacts>
        <artifact>
            <language>python</language>
            <identifier>factorial-script</identifier>
            <type>application/vnd.ant.code</type>
            <title>Python Factorial Calculator</title>
            <content>
def factorial(n):
    if n == 0 or n == 1:
        return 1
    else:
        return n * factorial(n - 1)

# Example usage
number = 5
result = factorial(number)
print(f"The factorial of {number} is {result}")
            </content>
        </artifact>
    </artifacts>
</response>
`;

console.log(xmlToJson(xml));

// function convertXmlToJson(xmlText) {
//     const parser = new DOMParser();
//     const xmlDoc = parser.parseFromString(xmlText, "text/xml");

//     function parseNode(node) {
//         if (node.nodeType === Node.TEXT_NODE) {
//             return node.nodeValue.trim();
//         }

//         const jsonObject = {};
//         if (node.attributes && node.attributes.length > 0) {
//             for (let i = 0; i < node.attributes.length; i++) {
//                 jsonObject[node.attributes[i].name] = node.attributes[i].value;
//             }
//         }

//         if (node.childNodes && node.childNodes.length > 0) {
//             for (let i = 0; i < node.childNodes.length; i++) {
//                 const childNode = node.childNodes[i];
//                 const childNodeName = childNode.nodeName;

//                 if (jsonObject[childNodeName] === undefined) {
//                     jsonObject[childNodeName] = parseNode(childNode);
//                 } else if (Array.isArray(jsonObject[childNodeName])) {
//                     jsonObject[childNodeName].push(parseNode(childNode));
//                 } else {
//                     jsonObject[childNodeName] = [jsonObject[childNodeName], parseNode(childNode)];
//                 }
//             }
//         }

//         return jsonObject;
//     }

//     return parseNode(xmlDoc.documentElement);
// }

// // Example usage:
// const xmlResponse = `
//   <response>
//     <observation>The user asked to search for information about AI on Wikipedia.</observation>
//     <mindspace>
//       Technological: Machine learning, AI applications
//       Scientific: Computer science, algorithms
//       Philosophical: Intelligence, human-AI interaction
//       Historical: AI development, research milestones
//     </mindspace>
//     <thought>Step 1) To search Wikipedia, use the 'search_wikipedia' tool.
//     Step 2) The relevant argument is the search query: 'artificial intelligence'.</thought>
//     <type>tool_calls</type>
//     <tool_calls>
//       <tool>
//         <name>Wikipedia</name>
//         <arguments>
//           <query>artificial intelligence</query>
//         </arguments>
//       </tool>
//     </tool_calls>
//     <artifacts></artifacts>
//   </response>
//   `;

// const jsonObject = convertXmlToJson(xmlResponse);
// console.log(JSON.stringify(jsonObject, null, 2));