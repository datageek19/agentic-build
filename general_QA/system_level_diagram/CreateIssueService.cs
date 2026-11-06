using System;
namespace fp
{
    [
    System.Web.Services.WebServiceBindingAttribute(
        Name = "CreateIssueWebService",
        Namespace = "MRWebServices")
    ]
    public class CreateIssueServiceCalller : System.Web.Services.Protocols.SoapHttpClientProtocol
    {
        public CreateIssueServiceCalller()
        {
            this.Url = "http://fakeserver/MRcgi/MRWebServices.pl";

        }
        [
        System.Web.Services.Protocols.SoapDocumentMethodAttribute(
            "MRWebServices#MRWebServices__createIssue",
            RequestNamespace = "MRWebServices",
            ResponseNamespace = "MRWebServices",
            Use = System.Web.Services.Description.SoapBindingUse.Encoded,
            ParameterStyle = System.Web.Services.Protocols.SoapParameterStyle.Wrapped)
        ]
        [return: System.Xml.Serialization.SoapElementAttribute("return")]
        public string MRWebServices__createIssue(
            string usr, string pw, string extraInfo, CreateIssueArgs args)
        {
            object[] results = this.Invoke(
                "MRWebServices__createIssue", new object[] { usr, pw, extraInfo, args });
            return ((string)(results[0]));
        }
    }
    [System.Xml.Serialization.SoapTypeAttribute("CreateIssueArgs", "MRWebServices")]
    public class CreateIssueArgs
    {
        public string projectID;
        public string title;
        public string description;
        public string status;
        public int priorityNumber;
    }
    public class CreateIssueService
    {
        public string createIssue(string projid, string title, string desc)
        {
            CreateIssueServiceCalller issue = new CreateIssueServiceCalller();

            CreateIssueArgs issueargs = new CreateIssueArgs();

            issueargs.projectID = projid;
            issueargs.title = title;
            issueargs.description = desc;
            issueargs.status = "Open";
            issueargs.priorityNumber = 1;

            string result = issue.MRWebServices__createIssue(
                "WebServices", "fakepassword", "", issueargs);
            return "Issue " + result + " has been created.";
        }
    }
}