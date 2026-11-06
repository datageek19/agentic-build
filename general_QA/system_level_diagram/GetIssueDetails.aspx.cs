using System;
using System.Web.UI.WebControls;
namespace fp
{
    public class GetIssueDetailsWebForm : System.Web.UI.Page
    {
        protected TextBox ProjID;
        protected TextBox IssueID;
        protected Button GetDetails;
        protected Label Msg;
        
        override protected void OnInit( EventArgs e )
        {
            this.GetDetails.Click += new System.EventHandler( this.GetDetails_Click );
            base.OnInit(e);
        }
        private void GetDetails_Click( object sender, System.EventArgs e )
        {
            GetIssueDetailsService s = new GetIssueDetailsService();
            Msg.Text = s.getIssueDetails( ProjID.Text, IssueID.Text );
        }
    }
}