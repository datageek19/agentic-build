using System.Reflection.Emit;

<%@ Page language = "c#" AutoEventWireup="false" Inherits="fp.EditIssueWebForm" %>

<html>

    <body>

        <form id="Form1" method="post" runat="server">

            <table>

                <tr>

                    <td>Workspace ID:</ td >

                    < td >< asp:TextBox id = "ProjID" runat="server"></asp:TextBox ></ td >

                </ tr >

                < tr >

                    < td > Issue ID:</ td >

                    < td >< asp:TextBox id = "IssueID" runat="server"></asp:TextBox ></ td >

                </ tr >

                < tr >

                    < td > NEW Title:</ td >

                    < td >< asp:TextBox id = "NewTitle" runat="server"></asp:TextBox ></ td >

                </ tr >

                < tr >

                    < td >< asp:Button id = "EditIssue" runat="server" Text="Edit Issue"></asp:Button ></ td >

                </ tr >

            </ table >

            < h2 >< asp:Label id = "Msg" runat="server"></asp:Label ></ h2 >

        </ form >

    </ body >

</ html >