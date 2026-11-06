using System;

using System.Web.UI.WebControls;

namespace fp

{

    public class EditIssueWebForm : System.Web.UI.Page

    {

        protected TextBox ProjID;

        protected TextBox IssueID;

        protected TextBox NewTitle;

        protected Button EditIssue;

        protected Label Msg;

        

        override protected void OnInit( EventArgs e )

        {

            this.EditIssue.Click += new System.EventHandler( this.EditIssue_Click );

            base.OnInit(e);

        }

        private void EditIssue_Click( object sender, System.EventArgs e )

        {

            EditIssueService s = new EditIssueService();

            s.editIssue( ProjID.Text, IssueID.Text, NewTitle.Text );

            Msg.Text = "Done editing issue " + IssueID.Text + ".";

        }

    }

}