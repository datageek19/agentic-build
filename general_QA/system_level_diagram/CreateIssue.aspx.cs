using System;
using System.Reflection.Emit;
using System.Web.UI.WebControls;
namespace fp
{
    public class CreateIssueWebForm : System.Web.UI.Page
    {
        protected TextBox ProjID;
        protected TextBox Title;
        protected TextBox Desc;
        protected Button Create;
        protected Label Msg;

        override protected void OnInit(EventArgs e)
        {
            this.Create.Click += new System.EventHandler(this.Create_Click);
            base.OnInit(e);
        }
        private void Create_Click(object sender, System.EventArgs e)
        {
            CreateIssueService s = new CreateIssueService();
            Msg.Text = s.createIssue(ProjID.Text, Title.Text, Desc.Text);
        }
    }
}